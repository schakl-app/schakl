"""A scriptable stand-in for the Cloudflare API (epic #278).

The cloudflare module must never touch the network in tests, and its interesting behaviour is
almost entirely *reconciliation* — adopt or create, drift or not, conflict or not. That needs a
Cloudflare that holds state and can be told to disagree with us, not a pile of one-off stubs.

State lives in plain dicts, so a test sets up "the zone already redirects" by writing the rule
into :attr:`FakeCloudflare.rulesets` and then asserting on what the module reports.

``deny`` is the other half: a tenant's API token is routinely scoped to *part* of what this
module wants, and the whole point of the status report is that it degrades per probe instead of
failing whole. Adding a path fragment to ``deny`` is how a test says "this token lacks that
permission".
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

PREFIX = "/client/v4"


def _ok(result: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "errors": [], "result": result})


def _err(status: int, message: str, code: int | None = None) -> httpx.Response:
    error: dict[str, Any] = {"message": message}
    if code is not None:
        error["code"] = code
    return httpx.Response(status, json={"success": False, "errors": [error], "result": None})


class FakeCloudflare:
    """One Cloudflare, shared by every token unless ``token_accounts`` says otherwise."""

    def __init__(self) -> None:
        #: Accounts a token may see, keyed by the token string. ``None`` = the default list.
        self.token_accounts: dict[str, list[dict]] = {}
        self.accounts: list[dict] = [{"id": "acct-1", "name": "Agency"}]
        #: cf_zone_id -> zone body.
        self.zones: dict[str, dict] = {}
        #: cf_zone_id -> list of dns records.
        self.dns: dict[str, list[dict]] = {}
        #: cf_zone_id -> the dynamic-redirect entrypoint ruleset (or absent).
        self.rulesets: dict[str, dict] = {}
        #: cf_zone_id -> list of legacy page rules.
        self.pagerules: dict[str, list[dict]] = {}
        #: account id -> list of Pages projects.
        self.pages: dict[str, list[dict]] = {}
        #: account id -> {project name -> [hostnames]}.
        self.pages_domains: dict[str, dict[str, list[dict]]] = {}
        #: Answer the project list *without* the embedded ``domains`` array. Cloudflare puts it
        #: there and the module reads it to avoid a call per project; this is how a test drives
        #: the fallback for a payload that omits it.
        self.pages_projects_omit_domains = False
        #: account id -> the Registrar domain list (#298). Cloudflare reports domains held at
        #: *other* registrars here too, which is exactly what the module has to tell apart.
        self.registrar: dict[str, list[dict]] = {}
        #: Path fragments this token is not allowed to touch → 403.
        self.deny: set[str] = set()
        #: Model an **account-owned** API token: ``GET /user/tokens/verify`` refuses it with the
        #: same 401/1000 it gives a dead token, while every other call it is scoped for works.
        #: It verifies at ``GET /accounts/{id}/tokens/verify`` instead. Cloudflare's real
        #: behaviour, and the reason a valid token used to read as "Token problem".
        self.account_owned_token = False
        #: Refuse every call with 401, whatever the token says — a token disabled or deleted at
        #: Cloudflare after schakl stored it. Flipping it back is how a test says "the admin
        #: fixed it at Cloudflare", which is the only way to exercise the *recovery* path.
        self.revoked = False
        #: Answer every call the way Cloudflare answers a malformed credential: 400/6003, before
        #: it ever looks the token up — deliberately *not* a 401 (observed against the live API).
        self.malformed_token = False
        #: The caller's IP, when the token carries a **Client IP Address Filter** that does not
        #: include it: 403/9109 at *every* endpoint (observed against the live API). Modelled as
        #: its own state rather than as an entry in ``deny`` because the two are opposites — a
        #: denied fragment is one endpoint this token is not scoped for, while this is a token
        #: that is scoped for everything and refused for all of it. A fake that could only
        #: express the first is a fake in which this failure does not exist.
        self.ip_blocked: str | None = None
        #: Every (method, path) that arrived, for asserting what was *not* called.
        self.calls: list[tuple[str, str]] = []

    # --- fixtures ---------------------------------------------------------------------- #
    def add_zone(
        self,
        name: str,
        *,
        zone_id: str | None = None,
        status: str = "active",
        account: str = "acct-1",
        name_servers: list[str] | None = None,
    ) -> str:
        zone_id = zone_id or f"zone-{uuid.uuid4().hex[:8]}"
        self.zones[zone_id] = {
            "id": zone_id,
            "name": name,
            "status": status,
            "paused": False,
            "plan": {"name": "Free Website"},
            "account": {"id": account, "name": "Agency"},
            "name_servers": name_servers or ["ana.ns.cloudflare.com", "bob.ns.cloudflare.com"],
            "original_name_servers": ["ns1.oud.nl", "ns2.oud.nl"],
        }
        self.dns.setdefault(zone_id, [])
        return zone_id

    def add_record(self, zone_id: str, **record: Any) -> dict:
        row = {
            "id": f"rec-{uuid.uuid4().hex[:8]}",
            "ttl": 1,
            "proxied": False,
            "comment": None,
            **record,
        }
        self.dns.setdefault(zone_id, []).append(row)
        return row

    def add_pages_domain(
        self,
        project: str,
        name: str,
        *,
        account: str = "acct-1",
        status: str = "active",
        error: str | None = None,
    ) -> dict:
        """A custom hostname already attached to a project — the state an agency arrives in.

        Somebody parked a domain on a placeholder Pages project in Cloudflare's own dashboard
        long before schakl saw the account, and nothing here created it.
        """
        row: dict[str, Any] = {
            "id": f"pd-{uuid.uuid4().hex[:6]}",
            "name": name,
            "status": status,
        }
        if error is not None:
            row["validation_data"] = {"error_message": error}
        self.pages_domains.setdefault(account, {}).setdefault(project, []).append(row)
        return row

    def add_redirect_rule(self, zone_id: str, rule: dict, *, ruleset_id: str = "rs-1") -> dict:
        ruleset = self.rulesets.setdefault(
            zone_id,
            {
                "id": ruleset_id,
                "name": "default",
                "phase": "http_request_dynamic_redirect",
                "rules": [],
            },
        )
        row = {"id": f"rule-{uuid.uuid4().hex[:8]}", **rule}
        ruleset["rules"].append(row)
        return row

    def add_registration(
        self,
        name: str,
        *,
        account: str = "acct-1",
        registrar: str | None = "Cloudflare",
        expires_at: str | None = "2027-03-01T23:59:59Z",
        auto_renew: bool | None = True,
        locked: bool | None = False,
    ) -> dict:
        """One row of the Registrar list. ``registrar=None`` is a domain Cloudflare knows about
        but does not hold — the case that must never start an invoice (#298)."""
        row = {
            "id": f"reg-{uuid.uuid4().hex[:8]}",
            "name": name,
            "current_registrar": registrar,
            "expires_at": expires_at,
            "auto_renew": auto_renew,
            "locked": locked,
            "registry_statuses": "ok,clientTransferProhibited",
        }
        self.registrar.setdefault(account, []).append(row)
        return row

    # --- transport --------------------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911, PLR0912
        path = request.url.path[len(PREFIX):]
        method = request.method
        self.calls.append((method, path))
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if self.malformed_token:
            return _err(400, "Invalid format for Authorization header", 6003)
        # The IP filter is part of authenticating the token, so it refuses everything — including
        # both verify endpoints. A valid, fully scoped credential looks identical to a dead one
        # from the outside; only the code tells them apart.
        if self.ip_blocked:
            return _err(403, f"Cannot use the access token from location: {self.ip_blocked}", 9109)
        # A token Cloudflare does not accept is refused at **every** endpoint, not just at the
        # verify one. Modelling it as "verify says no, everything else works" is precisely the
        # fiction that let a valid account-owned token read as a dead one for a release: the
        # only test that could have caught it was passing against a Cloudflare that does not
        # exist. If a probe rejects a token, so must every other path here.
        if token.startswith("bad-token") or self.revoked:
            return _err(401, "Invalid API Token", 1000)
        for fragment in self.deny:
            if fragment in path:
                return _err(403, "Actor is not authorized to perform this action", 10000)
        body = json.loads(request.content) if request.content else {}
        parts = [p for p in path.split("/") if p]

        if path == "/user/tokens/verify":
            # An account-owned token is refused here and nowhere else — Cloudflare's own
            # behaviour, and the whole reason ``verify_token`` takes an account id.
            if self.account_owned_token:
                return _err(401, "Invalid API Token", 1000)
            return _ok({"id": "tok-1", "status": "active"})

        # /accounts/{id}/tokens/verify — where an account-owned token verifies.
        if len(parts) == 4 and parts[0] == "accounts" and parts[2:] == ["tokens", "verify"]:
            return _ok({"id": "tok-1", "status": "active"})

        if path == "/accounts":
            return _ok(self.token_accounts.get(token, self.accounts))

        # /accounts/{id}/pages/...
        if len(parts) >= 4 and parts[0] == "accounts" and parts[2] == "pages":
            return self._pages(method, parts, body)

        # /accounts/{id}/registrar/domains
        if len(parts) >= 3 and parts[0] == "accounts" and parts[2] == "registrar":
            return _ok(self.registrar.get(parts[1], []))

        if path == "/zones":
            if method == "POST":
                return self._create_zone(body)
            return _ok(self._list_zones(request))

        if len(parts) >= 2 and parts[0] == "zones":
            return self._zone_scoped(method, parts, body, request)

        return _err(404, f"unhandled path {path}")

    # --- zones -------------------------------------------------------------------------- #
    def _list_zones(self, request: httpx.Request) -> list[dict]:
        params = request.url.params
        rows = list(self.zones.values())
        if params.get("name"):
            rows = [z for z in rows if z["name"] == params["name"]]
        if params.get("account.id"):
            rows = [z for z in rows if z["account"]["id"] == params["account.id"]]
        return rows

    def _create_zone(self, body: dict) -> httpx.Response:
        name = body.get("name")
        account = (body.get("account") or {}).get("id") or "acct-1"
        for zone in self.zones.values():
            if zone["name"] == name and zone["account"]["id"] == account:
                return _err(400, "The zone already exists on this account", 1061)
        zone_id = self.add_zone(name, status="pending", account=account)
        return _ok(self.zones[zone_id])

    def _zone_scoped(
        self, method: str, parts: list[str], body: dict, request: httpx.Request
    ) -> httpx.Response:
        zone_id = parts[1]
        if zone_id not in self.zones:
            return _err(404, "Zone not found", 1049)
        tail = parts[2:]

        if not tail:
            return _ok(self.zones[zone_id])

        if tail[0] == "dns_records":
            return self._dns(method, zone_id, tail, body)

        if tail[0] == "rulesets":
            return self._rulesets(method, zone_id, tail, body)

        if tail[0] == "pagerules":
            return _ok(self.pagerules.get(zone_id, []))

        return _err(404, f"unhandled zone path {'/'.join(tail)}")

    def _dns(self, method: str, zone_id: str, tail: list[str], body: dict) -> httpx.Response:
        records = self.dns.setdefault(zone_id, [])
        if len(tail) == 2 and tail[1] == "export":
            lines = [f"{r['name']}. 1 IN {r['type']} {r['content']}" for r in records]
            return httpx.Response(200, text=";; Zone file\n" + "\n".join(lines) + "\n")
        if len(tail) == 1:
            if method == "GET":
                return _ok(records)
            if method == "POST":
                if any(
                    r["name"] == body.get("name") and r["type"] == body.get("type")
                    for r in records
                ):
                    return _err(400, "Record already exists.", 81053)
                return _ok(self.add_record(zone_id, **body))
        if len(tail) == 2:
            record = next((r for r in records if r["id"] == tail[1]), None)
            if record is None:
                return _err(404, "Record not found", 81044)
            if method == "PATCH":
                record.update(body)
                return _ok(record)
            if method == "DELETE":
                records.remove(record)
                return _ok({"id": tail[1]})
        return _err(405, "unhandled dns call")

    def _rulesets(
        self, method: str, zone_id: str, tail: list[str], body: dict
    ) -> httpx.Response:
        # /rulesets/phases/{phase}/entrypoint
        if len(tail) == 4 and tail[1] == "phases" and tail[3] == "entrypoint":
            ruleset = self.rulesets.get(zone_id)
            if method == "GET":
                if ruleset is None:
                    return _err(404, "Ruleset not found", 10000)
                return _ok(ruleset)
            if method == "PUT":
                created = {
                    "id": "rs-created",
                    "name": body.get("name", "default"),
                    "phase": tail[2],
                    "rules": [
                        {"id": f"rule-{uuid.uuid4().hex[:8]}", **rule}
                        for rule in body.get("rules", [])
                    ],
                }
                self.rulesets[zone_id] = created
                return _ok(created)
        # /rulesets/{id}/rules[/{rule_id}]
        if len(tail) >= 3 and tail[2] == "rules":
            ruleset = self.rulesets.get(zone_id)
            if ruleset is None or ruleset["id"] != tail[1]:
                return _err(404, "Ruleset not found", 10000)
            if len(tail) == 3 and method == "POST":
                rule = {"id": f"rule-{uuid.uuid4().hex[:8]}", **body}
                ruleset["rules"].append(rule)
                return _ok(ruleset)
            if len(tail) == 4:
                rule = next((r for r in ruleset["rules"] if r["id"] == tail[3]), None)
                if rule is None:
                    return _err(404, "Rule not found", 10000)
                if method == "PATCH":
                    rule.update(body)
                    return _ok(ruleset)
                if method == "DELETE":
                    ruleset["rules"].remove(rule)
                    return _ok(ruleset)
        return _err(405, "unhandled ruleset call")

    # --- Pages ---------------------------------------------------------------------------- #
    def _pages(self, method: str, parts: list[str], body: dict) -> httpx.Response:
        account = parts[1]
        # accounts/{a}/pages/projects[/{name}/domains[/{host}]]
        if len(parts) == 4:
            return _ok(
                [
                    project
                    if self.pages_projects_omit_domains
                    else {
                        **project,
                        "domains": [
                            host["name"]
                            for host in self.pages_domains.get(account, {}).get(
                                project.get("name", ""), []
                            )
                        ],
                    }
                    for project in self.pages.get(account, [])
                ]
            )
        if len(parts) >= 6 and parts[5] == "domains":
            project = parts[4]
            hosts = self.pages_domains.setdefault(account, {}).setdefault(project, [])
            if len(parts) == 6:
                if method == "GET":
                    return _ok(hosts)
                if method == "POST":
                    row = {"id": f"pd-{uuid.uuid4().hex[:6]}",
                           "name": body.get("name"), "status": "pending"}
                    hosts.append(row)
                    return _ok(row)
            if len(parts) == 7 and method == "DELETE":
                match = next((h for h in hosts if h["name"] == parts[6]), None)
                if match is None:
                    return _err(404, "Domain not found")
                hosts.remove(match)
                return _ok({})
        return _err(404, "unhandled pages call")
