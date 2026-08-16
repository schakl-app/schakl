"""A scriptable stand-in for the Tag Manager API v2.

Installed through ``app.integrations.google_tag_manager.client.set_transport``, which is the
module's only network seam. Unset, every call goes to ``tagmanager.googleapis.com``, so a test
that forgets to install this fails loudly on connect rather than quietly passing.

It stubs at the **transport**, not at ``acting_as`` and not at the service, and that is the point:
a request made through it travels the real OAuth client, the real path builder, the real paging
loop, the real fingerprint handling and the real error classifier. A fake one layer higher would
let a ``pageToken`` bug, a wrong list key or a misread ``reason`` through untouched — which is
exactly the class of bug that only shows up against the live API.

It is **stateful** rather than a table of canned responses, because half of what this integration
does is a sequence: resolve a workspace, create a trigger, read its id back, create a tag firing
on it, freeze a version, publish it. Canned responses cannot express "the tag the previous call
made", and a test that cannot express that cannot catch the recipe wiring the two together wrongly.

The shapes are taken from the API's own discovery document (revision 20260812) — the list key is
the singular noun, ``pageToken`` is the only paging control there is, and ``fingerprint`` rides
the query string.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint, not a secret

ACCOUNT = "6371679663"
CONTAINER = "261371074"
PUBLIC_ID = "GTM-NPGFR9W9"


def error(status: int, *, reason: str | None = None, message: str = "nope") -> httpx.Response:
    """A Google JSON error in the shape Tag Manager actually sends it."""
    body: dict[str, Any] = {"error": {"code": status, "message": message, "status": "ERROR"}}
    if reason:
        body["error"]["details"] = [{"reason": reason}]
    return httpx.Response(status, json=body)


class FakeTagManager:
    """One Tag Manager, held in memory, with the ids it hands back being the ids it remembers."""

    def __init__(self) -> None:
        self.accounts: list[dict[str, Any]] = [
            {"accountId": ACCOUNT, "name": "breik.", "path": f"accounts/{ACCOUNT}"}
        ]
        self.containers: list[dict[str, Any]] = [
            {
                "accountId": ACCOUNT,
                "containerId": CONTAINER,
                "publicId": PUBLIC_ID,
                "name": "breik. test",
                "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}",
                "usageContext": ["web"],
                "domainName": ["breik.nl"],
            }
        ]
        self.workspaces: list[dict[str, Any]] = [
            {
                "accountId": ACCOUNT,
                "containerId": CONTAINER,
                "workspaceId": "1",
                "name": "Default Workspace",
                "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/workspaces/1",
                "fingerprint": "ws-1",
            }
        ]
        #: ``{workspace id: [resource]}`` per kind, so a tag created in one workspace is not
        #: visible in another — which is the whole reason workspaces exist.
        self.tags: dict[str, list[dict[str, Any]]] = {}
        self.triggers: dict[str, list[dict[str, Any]]] = {}
        self.variables: dict[str, list[dict[str, Any]]] = {}
        self.built_ins: dict[str, list[str]] = {}
        self.versions: list[dict[str, Any]] = []
        self.live_version_id: str | None = None
        #: Set to refuse the *next* matching path — ``(fragment, response)``, first match wins.
        self.failures: list[tuple[str, httpx.Response]] = []
        #: How many pages a list answer is split across, to exercise the paging loop.
        self.pages = 1
        #: Every request that arrived: ``(method, path, query, body)``.
        self.calls: list[tuple[str, str, dict[str, list[str]], dict[str, Any]]] = []
        self._next_id = 100

    # --- scripting ------------------------------------------------------------------------- #

    def fail(self, fragment: str, response: httpx.Response) -> None:
        self.failures.append((fragment, response))

    def _id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _q, _b in self.calls if method is None or m == method]

    # --- transport ------------------------------------------------------------------------- #

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(TOKEN_ENDPOINT):
            # authlib refreshes before the first call because the stored access token is stale.
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.fake-access-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        parsed = urlparse(url)
        path = parsed.path.removeprefix("/tagmanager/v2/")
        query = parse_qs(parsed.query)
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        self.calls.append((request.method, path, query, body))

        for fragment, response in self.failures:
            if fragment in path:
                return response

        return self._route(request.method, path, query, body)

    # --- routing --------------------------------------------------------------------------- #

    def _route(
        self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]
    ) -> httpx.Response:
        if path == "accounts" and method == "GET":
            return self._page("account", self.accounts, query)

        if path == "accounts/containers:lookup":
            tag_id = (query.get("tagId") or [""])[0]
            for container in self.containers:
                if container["publicId"] == tag_id:
                    return httpx.Response(200, json=container)
            return error(404, message="not found")

        parts = path.split("/")

        if path.endswith("/containers") and method == "GET":
            account = parts[1]
            rows = [c for c in self.containers if c["accountId"] == account]
            return self._page("container", rows, query)

        if len(parts) == 4 and parts[2] == "containers" and method == "GET":
            for container in self.containers:
                if container["containerId"] == parts[3]:
                    return httpx.Response(200, json=container)
            return error(404, message="no such container")

        if path.endswith(":snippet"):
            return httpx.Response(200, json={"snippet": f"<!-- Google Tag Manager --> {PUBLIC_ID}"})

        if path.endswith("/versions:live"):
            if self.live_version_id is None:
                return error(404, message="no live version")
            return httpx.Response(200, json=self._version(self.live_version_id))

        if path.endswith("/version_headers"):
            headers = [
                {
                    "accountId": ACCOUNT,
                    "containerId": CONTAINER,
                    "containerVersionId": version["containerVersionId"],
                    "name": version["name"],
                    "numTags": str(len(version.get("tag") or [])),
                    "numTriggers": str(len(version.get("trigger") or [])),
                    "numVariables": str(len(version.get("variable") or [])),
                    "path": version["path"],
                }
                for version in self.versions
            ]
            return self._page("containerVersionHeader", headers, query)

        if path.endswith(":publish"):
            version_id = parts[-1].split(":")[0]
            self.live_version_id = version_id
            return httpx.Response(
                200, json={"containerVersion": self._version(version_id), "compilerError": False}
            )

        if path.endswith("/workspaces") and method == "GET":
            return self._page("workspace", self.workspaces, query)

        if path.endswith("/workspaces") and method == "POST":
            workspace_id = self._id()
            made = {
                "accountId": ACCOUNT,
                "containerId": CONTAINER,
                "workspaceId": workspace_id,
                "name": body.get("name") or "workspace",
                "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/workspaces/{workspace_id}",
                "fingerprint": f"ws-{workspace_id}",
            }
            self.workspaces.append(made)
            # A new GTM workspace is a **copy of the live container** plus whatever you then do
            # to it, not an empty slate. Modelled because the difference is visible: without it,
            # opening the tag list after schakl's first write would make the client's existing
            # tags appear to have vanished — a screen that lies, produced entirely by the fake.
            live = self._version(self.live_version_id) if self.live_version_id else {}
            self.tags[workspace_id] = [dict(row) for row in live.get("tag") or []]
            self.triggers[workspace_id] = [dict(row) for row in live.get("trigger") or []]
            self.variables[workspace_id] = [dict(row) for row in live.get("variable") or []]
            return httpx.Response(200, json=made)

        if path.endswith("/status"):
            workspace_id = parts[-2]
            changes = [
                {"changeStatus": "added", "tag": tag} for tag in self.tags.get(workspace_id, [])
            ] + [
                {"changeStatus": "added", "trigger": trigger}
                for trigger in self.triggers.get(workspace_id, [])
            ]
            return httpx.Response(200, json={"workspaceChange": changes})

        if path.endswith(":create_version"):
            workspace_id = parts[-1].split(":")[0]
            tags = self.tags.get(workspace_id, [])
            triggers = self.triggers.get(workspace_id, [])
            variables = self.variables.get(workspace_id, [])
            if not (tags or triggers or variables):
                # GTM's own answer for "nothing to freeze": 200, and no version at all.
                return httpx.Response(200, json={"compilerError": False})
            version_id = self._id()
            version = {
                "accountId": ACCOUNT,
                "containerId": CONTAINER,
                "containerVersionId": version_id,
                "name": body.get("name") or f"Version {version_id}",
                "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/versions/{version_id}",
                "tag": list(tags),
                "trigger": list(triggers),
                "variable": list(variables),
            }
            self.versions.append(version)
            return httpx.Response(200, json={"containerVersion": version, "compilerError": False})

        if path.endswith("/built_in_variables") and method == "POST":
            workspace_id = parts[-2]
            types = query.get("type") or []
            self.built_ins.setdefault(workspace_id, []).extend(types)
            return httpx.Response(
                200,
                json={"builtInVariable": [{"type": t, "name": t} for t in types]},
            )

        for collection, store, id_field in (
            ("tags", self.tags, "tagId"),
            ("triggers", self.triggers, "triggerId"),
            ("variables", self.variables, "variableId"),
        ):
            if path.endswith(f"/{collection}") and method == "GET":
                return self._page(id_field[:-2], store.get(parts[-2], []), query)
            if path.endswith(f"/{collection}") and method == "POST":
                workspace_id = parts[-2]
                made = dict(body)
                made[id_field] = self._id()
                made["path"] = f"{'/'.join(parts[:-1])}/{collection}/{made[id_field]}"
                made["fingerprint"] = f"fp-{made[id_field]}"
                made["accountId"] = ACCOUNT
                made["containerId"] = CONTAINER
                made["workspaceId"] = workspace_id
                store.setdefault(workspace_id, []).append(made)
                return httpx.Response(200, json=made)
            if f"/{collection}/" in path:
                workspace_id = parts[-3]
                resource_id = parts[-1]
                rows = store.setdefault(workspace_id, [])
                found = next((r for r in rows if r.get(id_field) == resource_id), None)
                if found is None:
                    return error(404, message=f"no such {collection[:-1]}")
                if method == "GET":
                    return httpx.Response(200, json=found)
                if method == "DELETE":
                    rows.remove(found)
                    return httpx.Response(204)
                if method == "PUT":
                    sent = (query.get("fingerprint") or [""])[0]
                    if sent and sent != found.get("fingerprint"):
                        return error(409, message="fingerprint mismatch")
                    found.update(body)
                    found["fingerprint"] = f"fp-{resource_id}-2"
                    return httpx.Response(200, json=found)

        return httpx.Response(200, json={})

    # --- helpers --------------------------------------------------------------------------- #

    def _version(self, version_id: str) -> dict[str, Any]:
        for version in self.versions:
            if version["containerVersionId"] == version_id:
                return version
        return {
            "accountId": ACCOUNT,
            "containerId": CONTAINER,
            "containerVersionId": version_id,
            "name": f"Version {version_id}",
            "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/versions/{version_id}",
            "tag": [],
            "trigger": [],
            "variable": [],
        }

    def _page(
        self, key: str, rows: list[dict[str, Any]], query: dict[str, list[str]]
    ) -> httpx.Response:
        """Split ``rows`` across ``self.pages`` responses joined by ``nextPageToken``.

        A client that ignores the token silently returns a prefix, and no assertion about the
        first page would ever notice — which is why this exists even though most tests use one.
        """
        if self.pages <= 1:
            return httpx.Response(200, json={key: rows})
        size = max(1, -(-len(rows) // self.pages))
        chunks = [rows[i : i + size] for i in range(0, len(rows), size)] or [[]]
        token = (query.get("pageToken") or [""])[0]
        index = int(token.rsplit("-", 1)[-1]) if token else 0
        index = min(index, len(chunks) - 1)
        payload: dict[str, Any] = {key: chunks[index]}
        if index + 1 < len(chunks):
            payload["nextPageToken"] = f"page-{index + 1}"
        return httpx.Response(200, json=payload)
