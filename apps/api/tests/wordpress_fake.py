"""A scriptable stand-in for a WordPress site with Rank Math (docs/WORDPRESS.md).

The wordpress module must never touch the network in tests, and its interesting behaviour is
almost entirely *degradation*: a credential that reaches core REST but not Rank Math, a site
without the MCP Adapter, a user who is not an administrator, a host that eats the
``Authorization`` header. That needs a WordPress that can be told to be each of those, not a
pile of one-off stubs.

**The fake rejects a bad credential everywhere.** That is not politeness, it is the whole
reason the fake can catch anything: a stand-in kinder than the real server is a stand-in the bug
hides in, and the specific bug this guards against is a probe that concludes "the token is
fine" from an endpoint it never authenticated against. Every route below checks the Basic
header first, and the four toggles turn *individual surfaces* off without ever making a wrong
password work.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.integrations.wordpress.client import supports_ai_visibility

#: The plugin file wordpress.org fixes for Rank Math, and the version that first shipped AI
#: Visibility. A fake on 1.0.272 is how a test says "installed, but too old".
RANKMATH_PLUGIN = "seo-by-rank-math/rank-math.php"
RANKMATH_VERSION = "1.0.275"


def _json(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _wp_error(code: str, message: str, status: int) -> httpx.Response:
    """WordPress's own error envelope. The ``code`` is what a probe stores and reasons about —
    ``rest_no_route`` (the plugin is not here) and ``rest_forbidden`` (you may not call it) are
    opposite diagnoses that share a 4xx, and a fake that returned a bare status could not tell
    them apart either."""
    return httpx.Response(
        status, json={"code": code, "message": message, "data": {"status": status}}
    )


class FakeWordPress:
    """One WordPress site. Toggles turn surfaces off; none of them makes a bad password work."""

    def __init__(
        self,
        *,
        username: str = "agency",
        app_password: str = "abcd EFGH ijkl MNOP qrst UVWX",
    ) -> None:
        self.username = username
        self.app_password = app_password

        # --- what this site has ---------------------------------------------------------- #
        #: WordPress 6.9 core registers `wp-abilities/v1`; an older site does not.
        self.has_abilities = True
        #: The MCP Adapter plugin: namespace `mcp`, one **route** per server under it. The
        #: fake deliberately serves a non-default server name, so a probe that assumed
        #: `mcp-adapter-default-server` fails — and it puts the namespace in the index exactly
        #: as the real adapter does (bare `mcp`), because the first probe matched `mcp/` and
        #: recorded every live site as having no adapter (docs/WORDPRESS.md §4).
        self.mcp_server: str | None = "agency-server"
        #: Rank Math's version, or None for "not installed at all".
        self.rankmath_version: str | None = RANKMATH_VERSION
        #: Rank Math is installed but its account is not connected to a Content AI
        #: subscription — a real state that answers 401 `aiv_unauthorized` on every AI
        #: Visibility route while the rest of the site is perfectly healthy.
        self.aiv_subscribed = True
        #: Whether this credential is a WordPress administrator. Every Rank Math AI Visibility
        #: route is `manage_options`, so an editor's password reaches `wp/v2` and nothing else.
        self.is_admin = True
        #: A host that strips the `Authorization` header — a bare 401 on a correct password,
        #: and one of the two failures that are otherwise indistinguishable from a revoked one.
        self.strips_auth_header = False
        #: Not a WordPress site at all (or REST disabled): everything 404s.
        self.is_wordpress = True
        #: Contact Form 7 installed.
        self.has_forms = True
        #: WPML installed: records carry a `lang`, and `?lang=` filters.
        self.multilingual = False

        #: Content, keyed by rest base then id — pages and posts plus one custom post type
        #: (`dienst`, on the base `diensten`) so the type lookup is exercised. ACF field
        #: values ride on `acf`, as ACF exposes them with "Show in REST API" on.
        self.content: dict[str, dict[int, dict]] = {
            "pages": {
                1: {
                    "id": 1, "type": "page", "slug": "home", "status": "publish",
                    "link": "https://klant.nl/", "title": "Home",
                    "content": "<h2>Welkom</h2>", "excerpt": "",
                    "modified_gmt": "2026-09-01T10:00:00", "parent": 0, "template": "",
                    "acf": {"subtitel": "Industriële testen", "slider": [{"titel": "Een"}]},
                    "meta": {"_acf_changed": False}, "featured_media": 6632, "lang": "nl",
                },
                2: {
                    "id": 2, "type": "page", "slug": "nieuwe-dienst", "status": "draft",
                    "link": "https://klant.nl/?page_id=2", "title": "Nieuwe dienst",
                    "content": "", "excerpt": "", "modified_gmt": "2026-09-02T10:00:00",
                    "parent": 0, "template": "", "acf": {"subtitel": ""}, "meta": {},
                    "featured_media": 0, "lang": "nl",
                },
            },
            "posts": {
                10: {
                    "id": 10, "type": "post", "slug": "nieuws", "status": "publish",
                    "link": "https://klant.nl/nieuws/", "title": "Nieuws",
                    "content": "<p>Hallo</p>", "excerpt": "", "modified_gmt": "2026-08-01T10:00:00",
                    "parent": 0, "acf": None, "meta": {}, "featured_media": 0, "lang": "nl",
                }
            },
            "diensten": {},
        }
        self.media: dict[int, dict] = {
            6632: {
                "id": 6632, "title": {"rendered": "hero.jpg"}, "mime_type": "image/jpeg",
                "source_url": "https://klant.nl/wp-content/uploads/hero.jpg",
                "alt_text": "Hero", "media_details": {"width": 1600, "height": 900},
                "date_gmt": "2026-01-01T00:00:00",
            }
        }
        self.forms: dict[int, dict] = {
            6584: {
                "id": 6584, "slug": "contactformulier", "title": "Contactformulier",
                "locale": "nl_NL",
                "form": "[text* your-name] [email* your-email] [submit \"Verstuur\"]",
                "mail": {"subject": "Nieuw bericht", "sender": "site@klant.nl",
                         "recipient": "info@klant.nl", "body": "[your-name]",
                         "additional_headers": "", "attachments": "", "use_html": False,
                         "exclude_blank": False, "active": True},
                "mail_2": {"subject": "", "sender": "", "recipient": "", "body": "",
                           "additional_headers": "", "attachments": "", "use_html": False,
                           "exclude_blank": False, "active": False},
                "messages": {"mail_sent_ok": "Bedankt."},
                "additional_settings": "",
            }
        }
        #: Every write the fake received, so a test can assert the body a route sent.
        self.writes: list[tuple[str, dict]] = []
        #: Abilities beyond Rank Math's: one read-only, one write. `acf/field-groups` is what
        #: ACF 6.8 registers; the write one stands in for `acf/create-field-group`.
        self.extra_abilities: list[dict] = [
            {
                "name": "core/get-environment-info", "label": "Environment",
                "description": "", "category": "site",
                "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "core/get-site-info", "label": "Site", "description": "",
                "category": "site", "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "acf/field-groups", "label": "Field groups", "description": "",
                "category": "acf", "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "acf/create-field-group", "label": "Create field group",
                "description": "", "category": "acf",
                "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
                "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": False}},
            },
        ]
        self.ability_runs: list[tuple[str, str, object]] = []
        #: Plugin routes the curated surface knows nothing about, for the passthrough:
        #: ``(method, path) -> body``. `wp/v2/settings` stands in for the takeover routes.
        self.extra_routes: dict[tuple[str, str], object] = {
            ("GET", "/wp-json/wpml/v1/languages"): [{"code": "nl"}, {"code": "en"}],
            ("POST", "/wp-json/litespeed/v1/purge"): {"purged": True},
            ("GET", "/wp-json/wp/v2/settings"): {"title": "Klant BV", "email": "info@klant.nl"},
            ("POST", "/wp-json/wp/v2/settings"): {"title": "Overgenomen"},
            ("POST", "/wp-json/wp/v2/users"): {"id": 99, "roles": ["administrator"]},
            ("GET", "/wp-json/wp/v2/users"): [{"id": 1, "name": "Agency"}],
            ("GET", "/wp-json/big/v1/rows"): [{"n": i, "pad": "x" * 2000} for i in range(400)],
        }

        #: Rank Math brands, in the `/overview` row shape the plugin's `map_overview_brand()`
        #: produces.
        self.brands: list[dict] = [
            {
                "id": "brand-1",
                "name": "Klant BV",
                "url": "https://klant.nl",
                "locale": "NL",
                "status": "active",
                "score": 42.5,
                "rank": 3,
                # 0-100 (docs/WORDPRESS.md §3), the scale the plugin's own badge renders.
                "avg_sentiment": 62.0,
                "mentions": 18,
                "citations": 7,
                "last_analyzed": "2026-08-10T04:00:00Z",
                "analysis_status": "success",
            }
        ]
        self.summary: dict = {"tracked_brands": 1}

        #: Every path this fake was asked for, in order — so a test can assert that a probe
        #: really did ask each surface rather than inferring one from another.
        self.calls: list[str] = []
        #: How many times `/overview` was asked to bypass Rank Math's 12-hour cache. The one
        #: assertion that catches a sync built on the *ability* instead of the REST route.
        self.refresh_calls = 0

    # --- auth ---------------------------------------------------------------------------- #
    def _authorised(self, request: httpx.Request) -> bool:
        if self.strips_auth_header:
            return False
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:]).decode()
        except Exception:  # noqa: BLE001
            return False
        user, _, password = raw.partition(":")
        return user == self.username and password == self.app_password

    # --- transport ----------------------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)

        if not self.is_wordpress:
            return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

        # Checked before anything else, on every route. A fake that authenticated only the
        # routes it felt were sensitive would let a half-probed credential pass.
        if not self._authorised(request):
            return _wp_error(
                "rest_not_logged_in", "You are not currently logged in.", 401
            )

        if (request.method, path) in self.extra_routes:
            if request.method != "GET":
                import json as _json_mod

                self.writes.append((path, _json_mod.loads(request.content or b"null")))
            return _json(self.extra_routes[(request.method, path)])
        if path in ("/wp-json", "/wp-json/"):
            return _json(self._index())
        if path == "/wp-json/wp/v2/users/me":
            return _json(self._me())
        if path.startswith("/wp-json/wp-abilities/v1/abilities"):
            if not self.has_abilities:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return self._abilities_route(request, path)
        if path == "/wp-json/wp/v2/types":
            return _json(self._types())
        if path.startswith("/wp-json/wp/v2/media"):
            return self._media_route(request, path)
        if path.startswith("/wp-json/wp/v2/") and path.split("/")[4] in self.content:
            return self._content_route(request, path)
        if path.startswith("/wp-json/contact-form-7/v1/contact-forms"):
            if not self.has_forms:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return self._forms_route(request, path)
        if path == "/wp-json/wp/v2/plugins":
            if not self.is_admin:
                return _wp_error(
                    "rest_cannot_view_plugins", "Sorry, you are not allowed to manage plugins.", 403
                )
            return _json(self._plugins())
        if path.startswith("/wp-json/rankmath/v1/ai-visibility"):
            return self._ai_visibility(request, path)

        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    # --- surfaces ------------------------------------------------------------------------ #
    def _index(self) -> dict:
        namespaces = ["oembed/1.0", "wp/v2"]
        if self.has_abilities:
            namespaces.append("wp-abilities/v1")
        if self.rankmath_version:
            namespaces.append("rankmath/v1")
        routes: dict[str, dict] = {"/wp/v2": {"endpoints": [{"methods": ["GET"]}]}}
        if self.has_forms:
            namespaces.append("contact-form-7/v1")
        if self.multilingual:
            namespaces.append("wpml/v1")
        if self.mcp_server:
            namespaces.append("mcp")
            routes["/mcp"] = {"endpoints": [{"methods": ["GET"]}]}
            # A metadata-only route beside the transport, as breik.nl has (`mcp-oauth-server`):
            # the probe must pick the one that takes POST.
            routes["/mcp/mcp-oauth-server"] = {"endpoints": [{"methods": ["GET"]}]}
            routes[f"/mcp/{self.mcp_server}"] = {
                "endpoints": [{"methods": ["POST", "GET", "DELETE"]}]
            }
        return {
            "name": "Klant BV",
            "description": "Testen en inspecties",
            "url": "https://klant.nl",
            "namespaces": namespaces,
            "routes": routes,
            "authentication": {"application-passwords": {"endpoints": {}}},
        }

    def _me(self) -> dict:
        capabilities = {"read": True, "edit_posts": True}
        if self.is_admin:
            capabilities |= {"manage_options": True, "activate_plugins": True}
        return {
            "id": 1,
            "name": "Agency",
            "slug": self.username,
            "capabilities": capabilities,
        }

    def _abilities(self) -> list[dict]:
        rows = list(self.extra_abilities)
        if self.rankmath_version:
            rows += [
                {
                    "name": name, "label": name, "description": "", "category": "rank-math",
                    "input_schema": {}, "output_schema": {},
                    "meta": {"show_in_rest": True, "annotations": {"readonly": readonly}},
                }
                for name, readonly in (
                    ("rank-math/get-ai-visibility-overview", True),
                    ("rank-math/get-ai-visibility-brand-insights", True),
                    ("rank-math/get-ai-visibility-brand-queries", True),
                    ("rank-math/create-ai-visibility-brand", False),
                )
            ]
        return rows

    def _abilities_route(self, request: httpx.Request, path: str) -> httpx.Response:
        rows = self._abilities()
        tail = path[len("/wp-json/wp-abilities/v1/abilities") :].strip("/")
        if not tail:
            per_page = int(request.url.params.get("per_page", "10"))
            page = int(request.url.params.get("page", "1"))
            chunk = rows[(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json=chunk, headers={"X-WP-Total": str(len(rows))})
        run = tail.endswith("/run")
        name = tail[: -len("/run")] if run else tail
        spec = next((r for r in rows if r["name"] == name), None)
        if spec is None:
            return _wp_error("rest_ability_not_found", "Ability not found.", 404)
        if not run:
            return _json(spec)
        annotations = spec["meta"]["annotations"]
        expected = "GET" if annotations.get("readonly") else "POST"
        if request.method != expected:
            return _wp_error(
                "rest_ability_invalid_method", f"{expected} required.", 405
            )
        if expected == "GET":
            payload = {
                k[len("input[") : -1]: v
                for k, v in request.url.params.multi_items()
                if k.startswith("input[")
            }
        else:
            import json as _json_mod

            payload = (_json_mod.loads(request.content or b"{}") or {}).get("input")
        self.ability_runs.append((request.method, name, payload))
        if name == "core/get-environment-info":
            return _json({"wp_version": "6.9.1", "php_version": "8.3.4"})
        if name == "core/get-site-info":
            return _json({"name": "Klant BV", "language": "nl_NL", "timezone": "Europe/Amsterdam"})
        return _json({"ok": True, "input": payload})

    def _types(self) -> dict:
        return {
            "page": {"name": "Pagina's", "slug": "page", "rest_base": "pages", "hierarchical": 1},
            "post": {"name": "Berichten", "slug": "post", "rest_base": "posts"},
            "dienst": {"name": "Diensten", "slug": "dienst", "rest_base": "diensten"},
            "attachment": {"name": "Media", "slug": "attachment", "rest_base": "media"},
        }

    @staticmethod
    def _edit_shape(row: dict) -> dict:
        """What `context=edit` answers: `{raw, rendered}` pairs where a list read gives one."""
        out = dict(row)
        for key in ("title", "content", "excerpt"):
            out[key] = {"raw": row.get(key, ""), "rendered": f"<p>{row.get(key, '')}</p>"}
        return out

    def _content_route(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.split("/")
        base = parts[4]
        rows = self.content[base]
        wp_id = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None
        if request.method == "GET" and wp_id is None:
            statuses = set(request.url.params.get("status", "publish").split(","))
            lang = request.url.params.get("lang")
            search = (request.url.params.get("search") or "").lower()
            found = [
                r for r in rows.values()
                if r["status"] in statuses
                and (not lang or r.get("lang") == lang)
                and (not search or search in r["title"].lower())
            ]
            found.sort(key=lambda r: r["modified_gmt"], reverse=True)
            per_page = int(request.url.params.get("per_page", "10"))
            page = int(request.url.params.get("page", "1"))
            chunk = found[(page - 1) * per_page : page * per_page]
            return httpx.Response(
                200, json=[self._edit_shape(r) for r in chunk],
                headers={"X-WP-Total": str(len(found))},
            )
        if request.method == "GET":
            row = rows.get(wp_id)
            if row is None:
                return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            return _json(self._edit_shape(row))
        if request.method == "POST":
            import json as _json_mod

            body = _json_mod.loads(request.content or b"{}")
            self.writes.append((path, body))
            if wp_id is None:
                wp_id = max(list(rows) + [100]) + 1
                row = {
                    "id": wp_id, "type": base.rstrip("s") if base != "diensten" else "dienst",
                    "slug": body.get("slug") or f"item-{wp_id}", "status": "draft",
                    "link": f"https://klant.nl/?p={wp_id}", "title": "", "content": "",
                    "excerpt": "", "modified_gmt": "2026-09-03T10:00:00", "parent": 0,
                    "acf": {}, "meta": {}, "featured_media": 0, "lang": body.get("lang", "nl"),
                }
                rows[wp_id] = row
            else:
                row = rows.get(wp_id)
                if row is None:
                    return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            if body.get("status") not in (None, "publish", "future", "draft", "pending", "private"):
                return _wp_error("rest_invalid_param", "Invalid parameter(s): status", 400)
            for key in ("title", "content", "excerpt", "slug", "status", "template", "parent",
                        "featured_media"):
                if key in body:
                    row[key] = body[key]
            if isinstance(body.get("acf"), dict):
                row["acf"] = {**(row.get("acf") or {}), **body["acf"]}
            if isinstance(body.get("meta"), dict):
                row["meta"] = {**(row.get("meta") or {}), **body["meta"]}
            return _json(self._edit_shape(row))
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    def _media_route(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.split("/")
        if len(parts) > 5 and parts[5].isdigit():
            row = self.media.get(int(parts[5]))
            if row is None:
                return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            return _json(row)
        rows = list(self.media.values())
        return httpx.Response(200, json=rows, headers={"X-WP-Total": str(len(rows))})

    def _form_read(self, row: dict) -> dict:
        """CF7's read shape: `properties.form` is `{content, fields}`; the write shape is flat."""
        fields = [
            {"type": "text*", "basetype": "text", "name": "your-name"},
            {"type": "email*", "basetype": "email", "name": "your-email"},
            {"type": "submit", "basetype": "submit", "name": ""},
        ]
        return {
            "id": row["id"], "slug": row["slug"], "title": row["title"], "locale": row["locale"],
            "properties": {
                "form": {"content": row["form"], "fields": fields},
                "mail": row["mail"], "mail_2": row["mail_2"], "messages": row["messages"],
                "additional_settings": {"content": row["additional_settings"], "settings": []},
            },
        }

    def _forms_route(self, request: httpx.Request, path: str) -> httpx.Response:
        if not self.is_admin:
            return _wp_error("wpcf7_forbidden", "You are not allowed to access contact forms.", 403)
        parts = path.split("/")
        wp_id = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None
        if request.method == "GET" and wp_id is None:
            return _json(
                [
                    {"id": r["id"], "slug": r["slug"], "title": r["title"], "locale": r["locale"]}
                    for r in self.forms.values()
                ]
            )
        if request.method == "GET":
            row = self.forms.get(wp_id)
            if row is None:
                return _wp_error("wpcf7_not_found", "Contact form not found.", 404)
            return _json(self._form_read(row))
        if request.method == "POST":
            import json as _json_mod

            body = _json_mod.loads(request.content or b"{}")
            self.writes.append((path, body))
            if wp_id is None:
                wp_id = max(list(self.forms) + [7000]) + 1
                row = {
                    "id": wp_id, "slug": f"form-{wp_id}", "title": "", "locale": "nl_NL",
                    "form": "", "mail": {}, "mail_2": {}, "messages": {}, "additional_settings": "",
                }
                self.forms[wp_id] = row
            else:
                row = self.forms.get(wp_id)
                if row is None:
                    return _wp_error(
                        "wpcf7_not_found", "The requested contact form was not found.", 404
                    )
            for key in ("title", "locale", "form", "mail", "mail_2", "messages",
                        "additional_settings"):
                if key in body and body[key] is not None:
                    row[key] = body[key]
            return _json(self._form_read(row) | {"config_errors": {}})
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    def _plugins(self) -> list[dict]:
        plugins = [
            {"plugin": "akismet/akismet", "status": "active", "name": "Akismet", "version": "5.3"}
        ]
        if self.rankmath_version:
            plugins.append(
                {
                    "plugin": RANKMATH_PLUGIN,
                    "status": "active",
                    "name": "Rank Math SEO",
                    "version": self.rankmath_version,
                }
            )
        return plugins

    def _ai_visibility(self, request: httpx.Request, path: str) -> httpx.Response:
        if not supports_ai_visibility(self.rankmath_version):
            # Absent *and* too old answer the same way, because on both the controller was never
            # registered — AI Visibility begins at 1.0.273. The fake used to serve these routes
            # for any version at all, which made it kinder than the real server on exactly the
            # state a test wants to describe: `test_an_old_rank_math…` had to switch the
            # subscription off as well, with a comment saying the routes do not exist either.
            return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
        if not self.is_admin:
            # What every AI Visibility route answers an editor: the routes exist, this user
            # may not call them. Nothing to do with the password.
            return _wp_error(
                "rest_forbidden", "Sorry, you are not allowed to do that.", 403
            )
        if not self.aiv_subscribed:
            return _wp_error(
                "aiv_unauthorized",
                "Rank Math account not connected. Please connect your account and try again.",
                401,
            )

        if path.endswith("/overview"):
            if request.url.params.get("refresh"):
                self.refresh_calls += 1
            return _json(
                {"success": True, "data": {"summary": self.summary, "brands": self.brands}}
            )

        # /brands/{id}/insights and /brands/{id}/queries — enough shape for gate 2 to build on.
        if path.endswith("/insights"):
            return _json({"success": True, "data": {"competitors": [], "query_results": []}})
        if path.endswith("/queries"):
            return _json({"success": True, "data": {"queries": [], "total": 0}})
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
