"""A scriptable stand-in for Meta's Graph and Marketing API.

Installed through ``app.core.metagraph.set_transport``, the integration's only network seam.
Unset, every call goes to ``graph.facebook.com``, so a test that forgets to install this fails
loudly on connect rather than quietly passing.

It stubs at the **transport**, so a request made through it travels the real path builder, the
real parameter encoding, the real paging loop, the real ``appsecret_proof`` and the real error
classifier. And it is **stateful**, because half of what this integration does is a sequence —
derive a Page token, upload two photos, attach them to a post, read the permalink back — and
canned responses cannot express "the photo the previous call made".

Two things it can do that a table of responses cannot, and both are the point of the tests
that use them:

* :meth:`FakeMeta.lose_next` makes a write **happen and then lose its answer** — the post is
  created and the caller gets a timeout. That is the failure Meta's lack of an idempotency key
  turns into a duplicate post, and the only way to test that it does not.
* :meth:`FakeMeta.fail_next` answers one request with a chosen Meta error, so a dead token, a
  rate limit and a rejected image are each one line.

**Not verified against the live API.** The shapes are taken from Meta's published reference
(docs/META.md); docs/META.md §11 is the checklist that turns them from a reading into a
measurement.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

APP_ID = "1029384756"
APP_SECRET = "fake-app-secret-0123456789abcdef"  # noqa: S105 — a fixture
BUSINESS_ID = "900100200300"
SYSTEM_TOKEN = "EAAfake-system-user-token-0000000000000000"  # noqa: S105 — a fixture
SYSTEM_USER_ID = "122100200300"

PAGE_OWN = "100000000000001"
PAGE_CLIENT = "100000000000002"
IG_CLIENT = "17841400000000002"
AD_ACCOUNT_CLIENT = "555000111"
AD_ACCOUNT_OWN = "555000222"

ALL_SCOPES = (
    "business_management",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
    "read_insights",
    "instagram_manage_insights",
    "ads_read",
    "ads_management",
    "pages_manage_ads",
)


def error(
    code: int,
    message: str = "nope",
    *,
    status: int = 400,
    subcode: int | None = None,
    kind: str = "OAuthException",
    user_title: str | None = None,
    user_msg: str | None = None,
    blame: list[list[str]] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """A Meta error in the shape Meta actually sends it."""
    body: dict[str, Any] = {
        "message": message,
        "type": kind,
        "code": code,
        "fbtrace_id": "AbCdEfGhIjK",
    }
    if subcode is not None:
        body["error_subcode"] = subcode
    if user_title:
        body["error_user_title"] = user_title
    if user_msg:
        body["error_user_msg"] = user_msg
    if blame:
        body["error_data"] = {"blame_field_specs": blame}
    return httpx.Response(status, json={"error": body}, headers=headers)


@dataclass
class Token:
    token: str
    kind: str = "SYSTEM_USER"
    subject_id: str = SYSTEM_USER_ID
    name: str = "schakl system user"
    scopes: tuple[str, ...] = ALL_SCOPES
    #: Unix seconds, or ``0`` for a token that never expires.
    expires_at: int = 0
    valid: bool = True
    app_id: str = APP_ID
    #: For a Page token: the Page it acts as.
    page_id: str | None = None


@dataclass
class Call:
    method: str
    path: str
    params: dict[str, str]
    data: dict[str, str]
    token: str | None
    files: list[str] = field(default_factory=list)


class FakeMeta:
    """One Meta, held in memory, with the ids it hands back being the ids it remembers."""

    def __init__(self) -> None:
        self.app_id = APP_ID
        self.app_secret = APP_SECRET
        self.tokens: dict[str, Token] = {SYSTEM_TOKEN: Token(SYSTEM_TOKEN)}
        self.business = {"id": BUSINESS_ID, "name": "breik. B.V."}
        self.pages: dict[str, dict[str, Any]] = {
            PAGE_OWN: {
                "id": PAGE_OWN,
                "name": "breik.",
                "username": "breik",
                "relation": "owned",
                "tasks": ["CREATE_CONTENT", "ANALYZE", "ADVERTISE", "MODERATE", "MANAGE"],
            },
            PAGE_CLIENT: {
                "id": PAGE_CLIENT,
                "name": "Nova Fietsen",
                "username": "novafietsen",
                "relation": "client",
                "tasks": ["CREATE_CONTENT", "ANALYZE", "ADVERTISE"],
                "instagram": {
                    "id": IG_CLIENT,
                    "username": "novafietsen",
                    "name": "Nova Fietsen",
                },
            },
        }
        self.ad_accounts: dict[str, dict[str, Any]] = {
            AD_ACCOUNT_CLIENT: {
                "account_id": AD_ACCOUNT_CLIENT,
                "id": f"act_{AD_ACCOUNT_CLIENT}",
                "name": "Nova Fietsen ads",
                "currency": "EUR",
                "timezone_name": "Europe/Amsterdam",
                "account_status": 1,
                "relation": "client",
                "default_dsa_payor": "Nova Fietsen B.V.",
                "default_dsa_beneficiary": "Nova Fietsen B.V.",
            },
            AD_ACCOUNT_OWN: {
                "account_id": AD_ACCOUNT_OWN,
                "id": f"act_{AD_ACCOUNT_OWN}",
                "name": "breik. ads",
                "currency": "EUR",
                "timezone_name": "Europe/Amsterdam",
                "account_status": 1,
                "relation": "owned",
            },
        }
        #: ``{post id: post}`` for Page posts, published and scheduled alike.
        self.posts: dict[str, dict[str, Any]] = {}
        self.photos: dict[str, dict[str, Any]] = {}
        self.videos: dict[str, dict[str, Any]] = {}
        #: Instagram containers and published media.
        self.containers: dict[str, dict[str, Any]] = {}
        self.ig_media: dict[str, dict[str, Any]] = {}
        #: Ads objects, by kind then id.
        self.campaigns: dict[str, dict[str, Any]] = {}
        self.adsets: dict[str, dict[str, Any]] = {}
        self.creatives: dict[str, dict[str, Any]] = {}
        self.ads: dict[str, dict[str, Any]] = {}
        self.insight_rows: list[dict[str, Any]] = []
        #: Page/Instagram metrics this Meta still serves. Anything else is code 100.
        self.metrics: dict[str, float] = {
            "page_media_view": 12_400,
            "page_total_media_view_unique": 8_100,
            "page_post_engagements": 640,
            "page_views_total": 310,
            "page_follows": 1_250,
            "views": 22_000,
            "reach": 9_400,
            "accounts_engaged": 720,
            "total_interactions": 1_100,
            "profile_links_taps": 45,
        }
        #: Media Meta fetched from a public address: ``[url]``. A test asserts on it.
        self.fetched: list[str] = []
        #: What a fetch of a public address should be answered with, to simulate Meta failing
        #: to reach an instance behind a gateway.
        self.container_status: str = "FINISHED"
        self.calls: list[Call] = []
        self.refreshes = 0
        self.require_proof = False
        self._ids = itertools.count(1)
        self._failures: list[tuple[str, str, httpx.Response]] = []
        self._losses: list[tuple[str, str]] = []
        self.usage_header: dict[str, str] = {}
        #: Rows per page on a list edge, so paging is exercised with a handful of rows.
        self.page_size = 50

    # --- scripting --------------------------------------------------------------------------- #

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def fail_next(self, method: str, path_part: str, response: httpx.Response) -> None:
        """Answer the next ``method`` request whose path contains ``path_part`` with this."""
        self._failures.append((method.upper(), path_part, response))

    def lose_next(self, method: str, path_part: str) -> None:
        """Carry out the next matching request, then **lose the answer** (a read timeout)."""
        self._losses.append((method.upper(), path_part))

    def paths(self, method: str | None = None) -> list[str]:
        return [call.path for call in self.calls if method is None or call.method == method.upper()]

    def writes(self, path_part: str = "") -> list[Call]:
        return [c for c in self.calls if c.method != "GET" and path_part in c.path]

    def add_token(self, token: str, **kwargs: Any) -> Token:
        entry = Token(token, **kwargs)
        self.tokens[token] = entry
        return entry

    def expire_in(self, token: str, days: float) -> None:
        self.tokens[token].expires_at = int(time.time() + days * 86_400)

    # --- the request ------------------------------------------------------------------------- #

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # ``/v26.0/me/accounts`` → ``me/accounts``. The version is asserted to be there:
        # an unversioned call would silently follow the app dashboard's setting.
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].startswith("v") and parts[0][1:].replace(".", "").isdigit():
            parts = parts[1:]
        elif parts and parts[0] not in ("video-upload",):
            return error(2635, "unversioned call", status=400)
        route = "/".join(parts)
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        data, files = self._body(request)
        token = None
        header = request.headers.get("authorization", "")
        if header.lower().startswith(("bearer ", "oauth ")):
            token = header.split(" ", 1)[1]
        method = request.method.upper()
        self.calls.append(Call(method, route, params, data, token, files))

        for index, (want_method, part, response) in enumerate(self._failures):
            if want_method == method and part in route:
                del self._failures[index]
                return response

        response = self._route(method, route, params, data, files, token, request)
        for index, (want_method, part) in enumerate(self._losses):
            if want_method == method and part in route:
                del self._losses[index]
                raise httpx.ReadTimeout("the answer was lost", request=request)
        if self.usage_header:
            response.headers.update(self.usage_header)
        return response

    @staticmethod
    def _body(request: httpx.Request) -> tuple[dict[str, str], list[str]]:
        raw = request.content
        content_type = request.headers.get("content-type", "")
        if not raw:
            return {}, []
        if content_type.startswith("application/x-www-form-urlencoded"):
            return {k: v[0] for k, v in parse_qs(raw.decode()).items()}, []
        if content_type.startswith("multipart/form-data"):
            boundary = content_type.split("boundary=", 1)[1].encode()
            fields: dict[str, str] = {}
            files: list[str] = []
            for part in raw.split(b"--" + boundary):
                if b"Content-Disposition" not in part:
                    continue
                head, _, body = part.partition(b"\r\n\r\n")
                name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
                if b"filename=" in head:
                    files.append(name)
                else:
                    fields[name] = body.rsplit(b"\r\n", 1)[0].decode()
            return fields, files
        return {}, []

    # --- routing ----------------------------------------------------------------------------- #

    def _route(
        self,
        method: str,
        route: str,
        params: dict[str, str],
        data: dict[str, str],
        files: list[str],
        token: str | None,
        request: httpx.Request,
    ) -> httpx.Response:
        if route == "debug_token":
            return self._debug_token(params)
        if route == "oauth/access_token":
            return self._exchange(params)
        if route.startswith("video-upload/"):
            return self._reel_upload(route, request)

        who = self.tokens.get(token or "")
        if who is None or not who.valid:
            return error(190, "Invalid OAuth access token.", subcode=467, status=400)
        if who.expires_at and who.expires_at < time.time():
            return error(
                190,
                "Error validating access token: Session has expired.",
                subcode=463,
                status=400,
            )
        proof = (data or params).get("appsecret_proof")
        if self.require_proof:
            expected = hmac.new(
                self.app_secret.encode(), (token or "").encode(), hashlib.sha256
            ).hexdigest()
            if proof != expected:
                return error(100, "Invalid appsecret_proof provided in the API argument")

        segments = route.split("/")
        head = segments[0]
        edge = segments[1] if len(segments) > 1 else ""
        args = {**params, **data}

        if head == "me":
            return self._me(edge, who, args)
        if head == self.business["id"]:
            return self._business(edge, who, args)
        if head in self.pages:
            return self._page(method, head, edge, who, args, files)
        if head.startswith("act_"):
            return self._ad_account(method, head[4:], edge, who, args, files)
        if head == IG_CLIENT or any(
            p.get("instagram", {}).get("id") == head for p in self.pages.values()
        ):
            return self._instagram(method, head, edge, who, args)
        return self._object(method, head, edge, who, args)

    # --- tokens ------------------------------------------------------------------------------ #

    def _debug_token(self, params: dict[str, str]) -> httpx.Response:
        if params.get("access_token") != f"{self.app_id}|{self.app_secret}":
            return error(190, "Invalid OAuth access token - Cannot parse access token")
        who = self.tokens.get(params.get("input_token", ""))
        if who is None:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "is_valid": False,
                        "error": {"code": 190, "message": "Invalid OAuth access token."},
                    }
                },
            )
        expired = bool(who.expires_at and who.expires_at < time.time())
        body: dict[str, Any] = {
            "app_id": who.app_id,
            "type": who.kind,
            "application": "schakl test app",
            "is_valid": who.valid and not expired,
            "issued_at": int(time.time()) - 3_600,
            "expires_at": who.expires_at,
            "data_access_expires_at": 0,
            "scopes": list(who.scopes),
            "user_id": who.subject_id,
        }
        if not body["is_valid"]:
            body["error"] = {"code": 190, "subcode": 463, "message": "Session has expired"}
        return httpx.Response(200, json={"data": body})

    def _exchange(self, params: dict[str, str]) -> httpx.Response:
        if params.get("client_id") != self.app_id or params.get("client_secret") != (
            self.app_secret
        ):
            return error(1, "Error validating client secret.", status=400)
        current = self.tokens.get(params.get("fb_exchange_token", ""))
        if current is None or not current.valid:
            return error(190, "Invalid OAuth access token.", subcode=467)
        if current.expires_at and current.expires_at < time.time():
            return error(190, "Session has expired", subcode=463)
        self.refreshes += 1
        fresh = f"EAAfake-refreshed-{self.refreshes:04d}-0000000000000000"
        lifetime = 60 * 86_400
        self.tokens[fresh] = Token(
            fresh,
            kind=current.kind,
            subject_id=current.subject_id,
            name=current.name,
            scopes=current.scopes,
            expires_at=int(time.time()) + lifetime,
        )
        return httpx.Response(
            200, json={"access_token": fresh, "token_type": "bearer", "expires_in": lifetime}
        )

    # --- discovery --------------------------------------------------------------------------- #

    def _page_payload(self, page: dict[str, Any], *, with_tasks: bool) -> dict[str, Any]:
        out = {
            "id": page["id"],
            "name": page["name"],
            "username": page.get("username"),
            "picture": {"data": {"url": f"https://cdn.example/{page['id']}.jpg"}},
        }
        if with_tasks:
            out["tasks"] = page.get("tasks", [])
        instagram = page.get("instagram")
        if instagram:
            out["instagram_business_account"] = {
                **instagram,
                "profile_picture_url": f"https://cdn.example/{instagram['id']}.jpg",
            }
        return out

    def _paged(
        self, rows: list[dict[str, Any]], args: dict[str, str], route: str
    ) -> httpx.Response:
        start = int(args.get("after") or 0)
        size = min(int(args.get("limit") or self.page_size), self.page_size)
        chunk = rows[start : start + size]
        body: dict[str, Any] = {"data": chunk}
        if start + size < len(rows):
            body["paging"] = {
                "cursors": {"after": str(start + size)},
                # Meta echoes the token in ``next``; the client must drop it, not follow it.
                "next": (
                    f"https://graph.facebook.com/v26.0/{route}"
                    f"?limit={size}&after={start + size}&access_token=LEAKED"
                ),
            }
        return httpx.Response(200, json=body)

    def _me(self, edge: str, who: Token, args: dict[str, str]) -> httpx.Response:
        if not edge:
            return httpx.Response(200, json={"id": who.subject_id, "name": who.name})
        if edge == "accounts":
            if "pages_show_list" not in who.scopes:
                return error(200, "Requires pages_show_list permission", status=403)
            rows = [self._page_payload(p, with_tasks=True) for p in self.pages.values()]
            return self._paged(rows, args, "me/accounts")
        if edge == "adaccounts":
            if "ads_read" not in who.scopes and "ads_management" not in who.scopes:
                return error(200, "Requires ads_read permission", status=403)
            return self._paged(list(self.ad_accounts.values()), args, "me/adaccounts")
        return error(100, f"Unknown path components: /{edge}", subcode=33)

    def _business(self, edge: str, who: Token, args: dict[str, str]) -> httpx.Response:
        if "business_management" not in who.scopes:
            return error(200, "Requires business_management permission", status=403)
        if not edge:
            return httpx.Response(200, json=self.business)
        relation = "client" if edge.startswith("client_") else "owned"
        if edge in ("owned_pages", "client_pages"):
            rows = [
                self._page_payload(p, with_tasks=False)
                for p in self.pages.values()
                if p["relation"] == relation
            ]
            return self._paged(rows, args, f"{self.business['id']}/{edge}")
        if edge in ("owned_ad_accounts", "client_ad_accounts"):
            rows = [a for a in self.ad_accounts.values() if a["relation"] == relation]
            return self._paged(rows, args, f"{self.business['id']}/{edge}")
        return error(100, f"Unknown path components: /{edge}", subcode=33)

    # --- pages ------------------------------------------------------------------------------- #

    def page_token(self, page_id: str) -> str:
        token = f"EAAfake-page-token-{page_id}-0000000000"
        if token not in self.tokens:
            self.tokens[token] = Token(
                token, kind="PAGE", subject_id=page_id, name=page_id, page_id=page_id
            )
        return token

    def _next_id(self) -> str:
        return str(700_000_000 + next(self._ids))

    def _page(
        self,
        method: str,
        page_id: str,
        edge: str,
        who: Token,
        args: dict[str, str],
        files: list[str],
    ) -> httpx.Response:
        page = self.pages[page_id]
        if not edge:
            fields = args.get("fields", "")
            if "access_token" in fields:
                if "pages_show_list" not in who.scopes:
                    return error(200, "Requires pages_show_list permission", status=403)
                return httpx.Response(
                    200, json={"id": page_id, "access_token": self.page_token(page_id)}
                )
            return httpx.Response(200, json=self._page_payload(page, with_tasks=False))
        acting = who.page_id == page_id
        if edge == "insights":
            return self._insights(args)
        if edge == "published_posts":
            rows = [
                self._post_payload(p)
                for p in self.posts.values()
                if p["page_id"] == page_id and p["is_published"]
            ]
            rows.sort(key=lambda r: r["created_time"], reverse=True)
            return self._paged(rows, args, f"{page_id}/published_posts")
        if edge == "scheduled_posts":
            rows = [
                self._post_payload(p)
                for p in self.posts.values()
                if p["page_id"] == page_id and not p["is_published"]
            ]
            return self._paged(rows, args, f"{page_id}/scheduled_posts")
        if edge == "feed" and method == "GET":
            rows = [
                self._post_payload(p)
                for p in self.posts.values()
                if p["page_id"] == page_id and p["is_published"]
            ]
            rows.sort(key=lambda r: r["created_time"], reverse=True)
            return self._paged(rows, args, f"{page_id}/feed")
        if method != "POST":
            return error(100, "Unsupported get request.", subcode=33)
        if not acting:
            return error(
                200,
                "(#200) Publishing requires a Page access token",
                status=403,
            )
        if "CREATE_CONTENT" not in page.get("tasks", []):
            return error(200, "(#200) The user has not authorized this action", status=403)

        scheduled = args.get("published") == "false" and args.get("scheduled_publish_time")
        if edge == "feed":
            if not (args.get("message") or args.get("link") or args.get("attached_media")):
                return error(100, "(#100) Must provide a message, link or media")
            attached = json.loads(args["attached_media"]) if args.get("attached_media") else []
            for entry in attached:
                if entry.get("media_fbid") not in self.photos:
                    return error(100, "Invalid media_fbid")
            post_id = f"{page_id}_{self._next_id()}"
            self.posts[post_id] = {
                "id": post_id,
                "page_id": page_id,
                "message": args.get("message", ""),
                "link": args.get("link"),
                "photos": [entry["media_fbid"] for entry in attached],
                "is_published": not scheduled,
                "scheduled_publish_time": int(args["scheduled_publish_time"])
                if scheduled
                else None,
                "created_time": _now(),
            }
            return httpx.Response(200, json={"id": post_id})
        if edge == "photos":
            if "source" not in files and not args.get("url"):
                return error(324, "(#324) Requires upload file")
            photo_id = self._next_id()
            self.photos[photo_id] = {
                "id": photo_id,
                "page_id": page_id,
                "from_bytes": "source" in files,
                "url": args.get("url"),
                "alt": args.get("alt_text_custom"),
            }
            if args.get("published") == "false" and not scheduled:
                return httpx.Response(200, json={"id": photo_id})
            post_id = f"{page_id}_{self._next_id()}"
            self.posts[post_id] = {
                "id": post_id,
                "page_id": page_id,
                "message": args.get("caption", ""),
                "link": None,
                "photos": [photo_id],
                "is_published": not scheduled,
                "scheduled_publish_time": int(args["scheduled_publish_time"])
                if scheduled
                else None,
                "created_time": _now(),
            }
            return httpx.Response(200, json={"id": photo_id, "post_id": post_id})
        if edge == "videos":
            if not args.get("file_url"):
                return error(100, "(#100) file_url is required")
            self.fetched.append(args["file_url"])
            video_id = self._next_id()
            self.posts[video_id] = {
                "id": video_id,
                "page_id": page_id,
                "message": args.get("description", ""),
                "link": None,
                "photos": [],
                "video": args["file_url"],
                "is_published": not scheduled,
                "scheduled_publish_time": int(args["scheduled_publish_time"])
                if scheduled
                else None,
                "created_time": _now(),
            }
            return httpx.Response(200, json={"id": video_id})
        if edge == "video_reels":
            phase = args.get("upload_phase")
            if phase == "start":
                video_id = self._next_id()
                self.videos[video_id] = {"id": video_id, "page_id": page_id, "uploaded": False}
                return httpx.Response(
                    200,
                    json={
                        "video_id": video_id,
                        "upload_url": f"https://rupload.facebook.com/video-upload/v26.0/{video_id}",
                    },
                )
            if phase == "finish":
                video = self.videos.get(args.get("video_id", ""))
                if video is None or not video["uploaded"]:
                    return error(100, "(#100) The video was not uploaded")
                is_scheduled = args.get("video_state") == "SCHEDULED"
                self.posts[video["id"]] = {
                    "id": video["id"],
                    "page_id": page_id,
                    "message": args.get("description", ""),
                    "link": None,
                    "photos": [],
                    "reel": True,
                    "is_published": not is_scheduled,
                    "scheduled_publish_time": int(args["scheduled_publish_time"])
                    if is_scheduled
                    else None,
                    "created_time": _now(),
                }
                return httpx.Response(200, json={"success": True})
        return error(100, f"Unknown path components: /{edge}", subcode=33)

    def _reel_upload(self, route: str, request: httpx.Request) -> httpx.Response:
        video_id = route.rsplit("/", 1)[-1]
        video = self.videos.get(video_id)
        if video is None:
            return error(100, "Unknown video", subcode=33)
        url = request.headers.get("file_url")
        if not url:
            return error(100, "file_url header is required")
        self.fetched.append(url)
        video["uploaded"] = True
        return httpx.Response(200, json={"success": True})

    def _post_payload(self, post: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": post["id"],
            "message": post.get("message", ""),
            "created_time": post["created_time"],
            "permalink_url": f"https://www.facebook.com/{post['id']}",
            "is_published": post["is_published"],
            "scheduled_publish_time": post.get("scheduled_publish_time"),
            "status_type": "added_photos" if post.get("photos") else "mobile_status_update",
            "likes": {"summary": {"total_count": 12}},
            "comments": {"summary": {"total_count": 3}},
            "shares": {"count": 2},
        }

    # --- instagram --------------------------------------------------------------------------- #

    def _instagram(
        self, method: str, account: str, edge: str, who: Token, args: dict[str, str]
    ) -> httpx.Response:
        if edge == "insights":
            return self._insights(args, instagram=True)
        if edge == "content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_usage": len(self.ig_media)}]})
        if edge == "media" and method == "GET":
            rows = sorted(self.ig_media.values(), key=lambda r: r["timestamp"], reverse=True)
            return self._paged(rows, args, f"{account}/media")
        if "instagram_content_publish" not in who.scopes:
            return error(10, "(#10) Application does not have permission", status=403)
        if edge == "media" and method == "POST":
            kind = args.get("media_type", "IMAGE")
            if kind == "CAROUSEL":
                children = [c for c in args.get("children", "").split(",") if c]
                if not children or any(c not in self.containers for c in children):
                    return error(100, "Invalid carousel children")
            else:
                url = args.get("image_url") or args.get("video_url")
                if not url:
                    return error(100, "(#100) image_url or video_url is required")
                self.fetched.append(url)
            container = self._next_id()
            self.containers[container] = {
                "id": container,
                "account": account,
                "caption": args.get("caption", ""),
                "kind": kind,
                "status": self.container_status,
                "child": args.get("is_carousel_item") == "true",
                "alt": args.get("alt_text"),
            }
            return httpx.Response(200, json={"id": container})
        if edge == "media_publish" and method == "POST":
            container = self.containers.get(args.get("creation_id", ""))
            if container is None:
                return error(100, "Invalid creation_id", subcode=33)
            if container["status"] != "FINISHED":
                return error(9007, "Media ID is not available", subcode=2207027)
            media_id = self._next_id()
            container["status"] = "PUBLISHED"
            self.ig_media[media_id] = {
                "id": media_id,
                "caption": container["caption"],
                "media_type": container["kind"],
                "permalink": f"https://www.instagram.com/p/{media_id}/",
                "timestamp": _now(),
            }
            return httpx.Response(200, json={"id": media_id})
        return error(100, f"Unknown path components: /{edge}", subcode=33)

    def _insights(self, args: dict[str, str], *, instagram: bool = False) -> httpx.Response:
        name = args.get("metric", "")
        if name not in self.metrics:
            return error(100, f"(#100) The value must be a valid insights metric: {name}")
        value = self.metrics[name]
        if instagram:
            return httpx.Response(
                200, json={"data": [{"name": name, "total_value": {"value": value}}]}
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": name,
                        "period": "day",
                        "values": [{"value": value / 2}, {"value": value / 2}],
                    }
                ]
            },
        )

    # --- any object by id -------------------------------------------------------------------- #

    def _object(
        self, method: str, object_id: str, edge: str, who: Token, args: dict[str, str]
    ) -> httpx.Response:
        if object_id in self.containers and not edge:
            container = self.containers[object_id]
            return httpx.Response(
                200, json={"id": object_id, "status_code": container["status"], "status": ""}
            )
        if object_id in self.ig_media and not edge:
            return httpx.Response(200, json=self.ig_media[object_id])
        post = self.posts.get(object_id)
        if post is not None and not edge:
            if method == "DELETE":
                del self.posts[object_id]
                return httpx.Response(200, json={"success": True})
            if method == "POST":
                if "message" in args:
                    post["message"] = args["message"]
                if "scheduled_publish_time" in args:
                    post["scheduled_publish_time"] = int(args["scheduled_publish_time"])
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json=self._post_payload(post))
        for store, name in (
            (self.campaigns, "campaign"),
            (self.adsets, "adset"),
            (self.creatives, "creative"),
            (self.ads, "ad"),
        ):
            if object_id in store:
                return self._ads_object(method, store, name, object_id, edge, args)
        return error(
            100,
            f"Unsupported request: object with ID '{object_id}' does not exist",
            subcode=33,
            kind="GraphMethodException",
        )

    # --- ads --------------------------------------------------------------------------------- #

    def _ad_account(
        self,
        method: str,
        account_id: str,
        edge: str,
        who: Token,
        args: dict[str, str],
        files: list[str],
    ) -> httpx.Response:
        account = self.ad_accounts.get(account_id)
        if account is None:
            return error(100, "Unknown ad account", subcode=33)
        if not edge:
            return httpx.Response(200, json=account)
        stores = {
            "campaigns": (self.campaigns, "campaign"),
            "adsets": (self.adsets, "adset"),
            "adcreatives": (self.creatives, "creative"),
            "ads": (self.ads, "ad"),
        }
        if edge == "insights":
            if "ads_read" not in who.scopes and "ads_management" not in who.scopes:
                return error(200, "Requires ads_read permission", status=403)
            return self._ads_insights(account_id, args)
        if edge == "adimages" and method == "POST":
            digest = hashlib.md5(json.dumps(sorted(args)).encode()).hexdigest()  # noqa: S324
            return httpx.Response(
                200,
                json={"images": {"image": {"hash": digest, "url": "https://cdn.example/ad.jpg"}}},
            )
        if edge not in stores:
            return error(100, f"Unknown path components: /{edge}", subcode=33)
        store, name = stores[edge]
        if method == "GET":
            if "ads_read" not in who.scopes and "ads_management" not in who.scopes:
                return error(200, "Requires ads_read permission", status=403)
            rows = [row for row in store.values() if row["account_id"] == account_id]
            wanted = args.get("filtering")
            if wanted:
                for clause in json.loads(wanted):
                    field_name, values = clause.get("field"), clause.get("value")
                    if field_name and isinstance(values, list):
                        # ``campaign.id`` filters the rows of that campaign, not the row
                        # whose own id it is.
                        key = field_name.replace(".", "_")
                        rows = [r for r in rows if str(r.get(key)) in {str(v) for v in values}]
            return self._paged(rows, args, f"act_{account_id}/{edge}")
        if "ads_management" not in who.scopes:
            return error(200, "Requires ads_management permission", status=403)
        problem = self._ads_validate(name, args)
        if problem is not None:
            return problem
        validate_only = "validate_only" in args.get("execution_options", "")
        if validate_only:
            return httpx.Response(200, json={"success": True})
        object_id = self._next_id()
        row: dict[str, Any] = {"id": object_id, "account_id": account_id, **_decoded(args)}
        row.pop("execution_options", None)
        row.pop("appsecret_proof", None)
        row.setdefault("status", "PAUSED")
        row["effective_status"] = row["status"]
        row["created_time"] = _now()
        store[object_id] = row
        return httpx.Response(200, json={"id": object_id, "success": True})

    def _ads_validate(self, name: str, args: dict[str, str]) -> httpx.Response | None:
        def missing(field_name: str, code: int = 100) -> httpx.Response:
            return error(
                code,
                "Invalid parameter",
                user_title="Ontbrekend veld",
                user_msg=f"{field_name} is verplicht.",
                blame=[[field_name]],
            )

        if name == "campaign":
            for required in ("name", "objective", "special_ad_categories"):
                if required not in args:
                    return missing(required)
            if not args["objective"].startswith("OUTCOME_"):
                return error(
                    100,
                    "Invalid parameter",
                    user_msg="Objective is not supported.",
                    blame=[["objective"]],
                )
        if name == "adset":
            for required in (
                "name",
                "campaign_id",
                "billing_event",
                "optimization_goal",
                "targeting",
            ):
                if required not in args:
                    return missing(required)
            if args["campaign_id"] not in self.campaigns:
                return error(100, "Invalid campaign", blame=[["campaign_id"]])
            targeting = json.loads(args["targeting"])
            countries = (targeting.get("geo_locations") or {}).get("countries") or []
            eu = {"NL", "BE", "DE", "FR", "ES", "IT", "AT", "IE", "PT", "LU", "DK", "SE", "FI"}
            if eu & set(countries):
                if not args.get("dsa_payor"):
                    return error(
                        3858079,
                        "Invalid parameter",
                        user_msg="De betaler ontbreekt voor advertenties in de EU.",
                        blame=[["dsa_payor"]],
                    )
                if not args.get("dsa_beneficiary"):
                    return error(
                        3858081,
                        "Invalid parameter",
                        user_msg="De begunstigde ontbreekt voor advertenties in de EU.",
                        blame=[["dsa_beneficiary"]],
                    )
            if "advantage_audience" not in json.dumps(targeting.get("targeting_automation") or {}):
                return error(
                    1870227,
                    "Invalid parameter",
                    user_msg="Advantage audience flag is required.",
                    blame=[["targeting", "targeting_automation"]],
                )
            budget = int(args.get("daily_budget") or args.get("lifetime_budget") or 0)
            campaign = self.campaigns[args["campaign_id"]]
            has_campaign_budget = campaign.get("daily_budget") or campaign.get("lifetime_budget")
            if not budget and not has_campaign_budget:
                return missing("daily_budget")
            if budget and budget < 100:
                return error(
                    1885272,
                    "Invalid parameter",
                    user_msg="Het budget is te laag.",
                    blame=[["daily_budget"]],
                )
        if name == "creative":
            if not (args.get("object_story_spec") or args.get("object_story_id")):
                return missing("object_story_spec")
        if name == "ad":
            for required in ("name", "adset_id", "creative"):
                if required not in args:
                    return missing(required)
            if args["adset_id"] not in self.adsets:
                return error(100, "Invalid ad set", blame=[["adset_id"]])
        return None

    def _ads_object(
        self,
        method: str,
        store: dict[str, dict[str, Any]],
        name: str,
        object_id: str,
        edge: str,
        args: dict[str, str],
    ) -> httpx.Response:
        row = store[object_id]
        if edge == "insights":
            return self._ads_insights(row["account_id"], args, scope={name: object_id})
        if edge in ("adsets", "ads") and method == "GET":
            child = self.adsets if edge == "adsets" else self.ads
            key = f"{name}_id"
            rows = [r for r in child.values() if str(r.get(key)) == object_id]
            return self._paged(rows, args, f"{object_id}/{edge}")
        if method == "DELETE":
            row["status"] = "DELETED"
            row["effective_status"] = "DELETED"
            return httpx.Response(200, json={"success": True})
        if method == "POST":
            if "validate_only" in args.get("execution_options", ""):
                return httpx.Response(200, json={"success": True})
            changed = _decoded(args)
            changed.pop("execution_options", None)
            changed.pop("appsecret_proof", None)
            row.update(changed)
            if "status" in changed:
                row["effective_status"] = (
                    "PENDING_REVIEW"
                    if name == "ad" and changed["status"] == "ACTIVE"
                    else changed["status"]
                )
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=row)

    def _ads_insights(
        self, account_id: str, args: dict[str, str], scope: dict[str, str] | None = None
    ) -> httpx.Response:
        rows = [r for r in self.insight_rows if r.get("account_id") == account_id]
        for key, value in (scope or {}).items():
            rows = [r for r in rows if str(r.get(f"{key}_id")) == value]
        return self._paged(rows, args, f"act_{account_id}/insights")


def _decoded(args: dict[str, str]) -> dict[str, Any]:
    """Form fields back into values, the way Meta stores them."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if value[:1] in "[{":
            try:
                out[key] = json.loads(value)
                continue
            except ValueError:
                pass
        out[key] = value
    return out


def _now() -> str:
    # Meta's own shape: an offset without its colon.
    return time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime())
