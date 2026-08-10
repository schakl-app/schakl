"""Cloudflare API v4 client, per **tenant** token (epic #278). Business-licensed — see LICENSE.

Not to be confused with :mod:`app.core.cloud.cloudflare`, which talks to Cloudflare with the
*operator's* instance-level token about the operator's own zone (epic #199). This one is the
opposite posture in every respect that matters, which is why it is a separate file rather than a
parameter on that one:

* **The credential is tenant data**, one row per :class:`~app.modules.cloudflare.models.
  CloudflareAccount`, encrypted at rest and handed in per call. A tenant holds several.
* **The zones are the tenant's clients'**, so nothing here is scoped to a configured zone id.
* **Least privilege is the tenant's to configure**, and their token will routinely be *partly*
  scoped. So every probe reports what it found rather than failing the screen: a token that can
  read zones and edit DNS but not list accounts is a perfectly useful token, it just cannot
  create a new zone. :func:`probe_capabilities` is what turns that into a sentence the admin can
  act on instead of a 403 at the button.

Rules that do not bend:

* **The token never reaches a log line, an exception message, or a response.** It lives in one
  header. Nothing here formats headers into an error, and :func:`CloudflareError` carries only
  Cloudflare's own error text.
* **Read-then-write, never blind PUT.** The redirect entrypoint ruleset holds the tenant's own
  rules too; we append/patch/delete *our* rule by id and never rewrite the list (which is what
  ``PUT /rulesets/phases/…/entrypoint`` would do).
* **Nothing is deleted at Cloudflare that schakl did not create.** Every destructive call takes
  an id this module stored earlier.
* **The network is off in tests.** :data:`_transport` is the only seam; unset, every call goes
  to ``api.cloudflare.com`` and a test that forgot to stub fails loudly on connect.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("schakl.cloudflare")

API_BASE = "https://api.cloudflare.com/client/v4"

#: Cloudflare is a dependency of a *screen*, not of a page load: fail fast rather than hold a
#: request open behind a slow edge.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)

#: Retried once, and only for conditions a retry can fix. Cloudflare's create calls are not
#: idempotent, so the retry sits below callers that tolerate a duplicate (find-then-create).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Hard cap on paginated reads. An unbounded read is a build break (CLAUDE.md §9); an agency
#: with more than 1000 zones in one Cloudflare account needs a background job, not a bigger loop.
MAX_PAGES = 20
PER_PAGE = 50

#: The capabilities :func:`probe_capabilities` can actually observe with a handful of cheap
#: calls. Deliberately short: everything else (can this token edit *this* zone's DNS?) cannot be
#: known without a zone in hand, and guessing would be worse than the real error at the call.
CAPABILITIES: tuple[str, ...] = (
    "token_valid",
    "accounts_read",
    "zones_read",
    "pages_read",
    "registrar_read",
)

#: Cloudflare's code for "this token carries a **Client IP Address Filter** and the address you
#: are calling from is not on it" — HTTP 403, *"Cannot use the access token from location: <ip>"*.
#: It is a 403 and it is the one 403 that says nothing whatsoever about scope: the token is
#: valid, correctly permissioned, and refused for **every** call from this network. Named here
#: because that difference is the entire diagnosis, and reading it as either "invalid token" or
#: "missing permission" sends an admin to re-mint a credential that was never the problem.
IP_RESTRICTED_CODE = 9109

#: The phase whose entrypoint ruleset holds Redirect Rules ("Single Redirects").
REDIRECT_PHASE = "http_request_dynamic_redirect"

#: Stamped on every rule this module creates, so a reconcile can tell our rule from the
#: tenant's own even if the stored id is lost. Never used to *delete* — that still needs the id.
RULE_MARKER = "schakl"


class CloudflareError(RuntimeError):
    """A Cloudflare call failed. ``message`` is Cloudflare's own error text (never the token)."""

    def __init__(
        self, message: str, *, status: int | None = None, code: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        #: Cloudflare's numeric error code, where it gave one — the only reliable way to tell
        #: "this zone already exists" (1061) from a generic 400.
        self.code = code


class CloudflareAuthError(CloudflareError):
    """The token is invalid, revoked, or lacks the scope this call needs.

    Its own class because the response is the opposite of every other failure: retrying cannot
    help and only the tenant can fix it, by re-minting the token with wider permissions.
    """


#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every client uses. Tests only."""
    global _transport
    _transport = transport


def _flatten_errors(body: Any) -> tuple[str, int | None]:
    """Cloudflare's error envelope → (text, first numeric code). Defensive: an edge error page
    is HTML, and a gateway timeout is not JSON at all."""
    if not isinstance(body, dict):
        return "", None
    errors = body.get("errors") or []
    if not isinstance(errors, list):
        return "", None
    parts: list[str] = []
    code: int | None = None
    for err in errors:
        if isinstance(err, dict):
            parts.append(str(err.get("message", err)))
            if code is None and isinstance(err.get("code"), int):
                code = err["code"]
            for chain in err.get("error_chain") or []:
                if isinstance(chain, dict) and chain.get("message"):
                    parts.append(str(chain["message"]))
        elif err:
            parts.append(str(err))
    return "; ".join(p for p in parts if p), code


class CloudflareClient:
    """One tenant token's worth of Cloudflare access. Cheap to construct, one per operation."""

    def __init__(self, token: str) -> None:
        self._token = token

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API_BASE,
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            transport=_transport,
        )

    # --- transport ----------------------------------------------------------------------- #
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """One authenticated call, returning Cloudflare's ``result``."""
        async with self._http() as http:
            return await self._send(http, method, path, json=json, params=params)

    async def _send(
        self,
        http: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        for attempt in (1, 2):
            try:
                response = await http.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                # str(exc) is a transport message ("connect timeout"), never the headers.
                if attempt == 1:
                    continue
                raise CloudflareError(f"Cloudflare unreachable: {exc}") from exc
            if response.status_code in _RETRY_STATUSES and attempt == 1:
                continue
            return self._unwrap(response)
        raise CloudflareError("Cloudflare unreachable")  # pragma: no cover — loop returns

    def _unwrap(self, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = None
        detail, code = _flatten_errors(body)
        if response.status_code in (401, 403):
            raise CloudflareAuthError(
                detail or f"Cloudflare rejected the token (HTTP {response.status_code})",
                status=response.status_code,
                code=code,
            )
        if response.status_code >= 400 or not (
            isinstance(body, dict) and body.get("success", False)
        ):
            raise CloudflareError(
                detail or f"Cloudflare returned HTTP {response.status_code}",
                status=response.status_code,
                code=code,
            )
        return body.get("result")

    async def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Every page of a list endpoint, capped at :data:`MAX_PAGES` (never truncated silently
        — hitting the cap raises, per CLAUDE.md §17's "over the limit is an error")."""
        rows: list[dict] = []
        async with self._http() as http:
            for page in range(1, MAX_PAGES + 1):
                query = {**(params or {}), "page": page, "per_page": PER_PAGE}
                result = await self._send(http, "GET", path, params=query) or []
                rows.extend(r for r in result if isinstance(r, dict))
                if len(result) < PER_PAGE:
                    return rows
        raise CloudflareError(
            f"Cloudflare returned more than {MAX_PAGES * PER_PAGE} rows for {path}"
        )

    # --- identity & capabilities ---------------------------------------------------------- #
    async def verify_token(self, account_id: str | None = None) -> dict:
        """Ask Cloudflare to describe this token — at whichever of the two endpoints owns it.

        Cloudflare has **two kinds of API token and they do not verify at the same URL**. A
        *user* token answers at ``GET /user/tokens/verify``. An **account-owned** token — the
        newer kind, owned by the account rather than by a person, which is exactly what an
        agency mints so that a departing employee cannot take the integration with them — is
        rejected there with ``401`` and code ``1000``, *"Invalid API Token"*, while working
        perfectly for every zone, DNS and account call it is scoped for. It verifies at
        ``GET /accounts/{account_id}/tokens/verify`` instead.

        So a 401 *here* means "asked the wrong endpoint" at least as often as it means "dead
        token", and the only way to tell the two apart is to ask the other one.
        """
        try:
            return await self.request("GET", "/user/tokens/verify") or {}
        except CloudflareAuthError:
            if not account_id:
                raise
            return await self.request("GET", f"/accounts/{account_id}/tokens/verify") or {}

    async def list_accounts(self) -> list[dict]:
        return await self.paginate("/accounts")

    async def probe_capabilities(
        self, account_id: str | None = None
    ) -> tuple[dict[str, bool], dict | None]:
        """What this token can be observed to do, plus the account it belongs to (if visible).

        A handful of cheap calls, **each failing softly and none of them the gate**. A token
        that cannot list accounts is *scoped*, not broken, and the admin needs to be told which
        of the two it is.

        That includes the verify call, which used to run first and raise for everyone behind
        it. It is a probe like the others: it answers "Cloudflare will describe this token to
        me", which is a strictly narrower question than "Cloudflare accepts this token" — an
        account-owned token whose account id we cannot read answers *no* to the first and *yes*
        to every call this module actually makes. So a successful read is taken as the better
        evidence it is, and only a token that was refused by **every** probe raises: that is
        the one state where "invalid" is the honest word rather than a guess.
        """
        caps = dict.fromkeys(CAPABILITIES, False)
        rejection: CloudflareAuthError | None = None

        # Accounts first, because the account-owned verify endpoint needs an id to address.
        account: dict | None = None
        try:
            accounts = await self.list_accounts()
        except CloudflareAuthError as exc:
            rejection = exc
            accounts = []
        else:
            caps["accounts_read"] = True
            account = accounts[0] if len(accounts) == 1 else None
            if len(accounts) > 1:
                # More than one account behind one token is legal and ambiguous; the caller
                # picks. Reported as the full list rather than silently taking the first.
                account = {"_multiple": accounts}

        discovered_id = account.get("id") if isinstance(account, dict) else None
        # The caller's pinned id wins: it is the one every real call in this module uses.
        account_id = account_id or (str(discovered_id) if discovered_id else None)

        try:
            await self.verify_token(account_id)
        except CloudflareAuthError as exc:
            rejection = rejection or exc
        else:
            caps["token_valid"] = True

        try:
            await self.request("GET", "/zones", params={"per_page": 1})
        except CloudflareAuthError as exc:
            rejection = rejection or exc
        else:
            caps["zones_read"] = True

        if account_id:
            try:
                await self.request(
                    "GET", f"/accounts/{account_id}/pages/projects", params={"per_page": 1}
                )
            except CloudflareAuthError:
                pass
            except CloudflareError:
                # Pages not enabled on the account answers 4xx, not 403 — same conclusion.
                pass
            else:
                caps["pages_read"] = True

            try:
                await self.request(
                    "GET", f"/accounts/{account_id}/registrar/domains", params={"per_page": 1}
                )
            except CloudflareError:
                # Registrar is its own token permission (#298) and an account that has never
                # registered anything through Cloudflare may answer 4xx outright. Either way
                # the conclusion is the same: this token is not evidence about registrations.
                pass
            else:
                caps["registrar_read"] = True

        if not any(caps.values()):
            # Every probe refused. Now — and only now — "the token is invalid" is a statement
            # about the token rather than about one endpoint's opinion of it.
            raise rejection or CloudflareAuthError("Cloudflare rejected the token", status=401)
        if not caps["token_valid"]:
            # Something answered, so Cloudflare accepts this token; we just could not reach a
            # verify endpoint that would say so out loud. The reads are better evidence anyway
            # — they are the calls the module makes.
            caps["token_valid"] = True
        return caps, account

    # --- zones ---------------------------------------------------------------------------- #
    async def list_zones(self, *, account_id: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if account_id:
            params["account.id"] = account_id
        return await self.paginate("/zones", params)

    async def find_zone(self, name: str) -> dict | None:
        """The zone for exactly this apex, or None.

        Cloudflare's ``name`` filter is exact, but ``name__endswith`` semantics have bitten this
        codebase before (``app.core.cloud.cloudflare.find_custom_hostname``), so the result is
        re-checked rather than trusted.
        """
        result = await self.request("GET", "/zones", params={"name": name, "per_page": 50})
        for row in result or []:
            if isinstance(row, dict) and row.get("name") == name:
                return row
        return None

    async def get_zone(self, zone_id: str) -> dict:
        return await self.request("GET", f"/zones/{zone_id}") or {}

    async def create_zone(self, name: str, account_id: str, *, jump_start: bool = False) -> dict:
        """Create a zone in ``account_id``.

        ``jump_start`` (Cloudflare's "scan existing DNS records") is **off** by default: it
        imports whatever public DNS currently answers, which for a domain mid-migration is the
        old host's records — quietly recreating the thing the migration is moving away from.
        """
        return (
            await self.request(
                "POST",
                "/zones",
                json={
                    "name": name,
                    "account": {"id": account_id},
                    "type": "full",
                    "jump_start": jump_start,
                },
            )
            or {}
        )

    # --- Registrar -------------------------------------------------------------------------- #
    async def list_registrar_domains(self, account_id: str) -> list[dict]:
        """Every domain Cloudflare Registrar knows about for this account (#298).

        A **different question from** :meth:`list_zones`, and the whole reason this call exists:
        a zone says Cloudflare answers DNS for a name, this says who holds its registration.
        The reply carries domains registered elsewhere too (that is what ``current_registrar``
        is for), so a caller must read that field rather than the list's membership.

        Never exercised against a live Registrar account — the parsing on the other side of
        this call is defensive for the reason ``docs/OXXA.md`` §1 states, and
        ``docs/CLOUDFLARE.md`` carries the checklist to run the day one exists.
        """
        return await self.paginate(f"/accounts/{account_id}/registrar/domains")

    # --- DNS ------------------------------------------------------------------------------ #
    async def list_dns_records(self, zone_id: str) -> list[dict]:
        return await self.paginate(f"/zones/{zone_id}/dns_records")

    async def create_dns_record(self, zone_id: str, record: dict) -> dict:
        return await self.request("POST", f"/zones/{zone_id}/dns_records", json=record) or {}

    async def update_dns_record(self, zone_id: str, record_id: str, record: dict) -> dict:
        return (
            await self.request(
                "PATCH", f"/zones/{zone_id}/dns_records/{record_id}", json=record
            )
            or {}
        )

    async def delete_dns_record(self, zone_id: str, record_id: str) -> None:
        await self.request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")

    async def export_dns(self, zone_id: str) -> str:
        """The zone as a BIND file. This endpoint answers ``text/plain``, not the JSON
        envelope, so it bypasses :meth:`_unwrap` entirely."""
        async with self._http() as http:
            try:
                response = await http.get(f"/zones/{zone_id}/dns_records/export")
            except httpx.HTTPError as exc:
                raise CloudflareError(f"Cloudflare unreachable: {exc}") from exc
            if response.status_code in (401, 403):
                raise CloudflareAuthError(
                    "Cloudflare rejected the token for the zone export",
                    status=response.status_code,
                )
            if response.status_code >= 400:
                detail, code = _flatten_errors(
                    response.json() if response.headers.get("content-type", "").startswith(
                        "application/json"
                    ) else None
                )
                raise CloudflareError(
                    detail or f"Cloudflare returned HTTP {response.status_code}",
                    status=response.status_code,
                    code=code,
                )
            return response.text

    # --- redirect rules (Single Redirects) ------------------------------------------------- #
    async def get_redirect_ruleset(self, zone_id: str) -> dict | None:
        """The zone's dynamic-redirect entrypoint ruleset, or None when it has none yet.

        A zone with no redirect rules has no entrypoint ruleset at all and answers 404 — which
        is a normal state, not an error.
        """
        try:
            return await self.request(
                "GET", f"/zones/{zone_id}/rulesets/phases/{REDIRECT_PHASE}/entrypoint"
            )
        except CloudflareError as exc:
            if exc.status == 404:
                return None
            raise

    async def create_redirect_ruleset(self, zone_id: str, rule: dict) -> dict:
        """Create the entrypoint ruleset **with** our single rule.

        Only ever called when :meth:`get_redirect_ruleset` answered None, so this PUT cannot
        overwrite rules the tenant wrote by hand — the case that makes a blind PUT unsafe.
        """
        return (
            await self.request(
                "PUT",
                f"/zones/{zone_id}/rulesets/phases/{REDIRECT_PHASE}/entrypoint",
                json={
                    "name": "default",
                    "kind": "zone",
                    "phase": REDIRECT_PHASE,
                    "rules": [rule],
                },
            )
            or {}
        )

    async def add_redirect_rule(self, zone_id: str, ruleset_id: str, rule: dict) -> dict:
        """Append one rule to an existing ruleset, leaving the tenant's own rules untouched."""
        return (
            await self.request(
                "POST", f"/zones/{zone_id}/rulesets/{ruleset_id}/rules", json=rule
            )
            or {}
        )

    async def update_redirect_rule(
        self, zone_id: str, ruleset_id: str, rule_id: str, rule: dict
    ) -> dict:
        return (
            await self.request(
                "PATCH", f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{rule_id}", json=rule
            )
            or {}
        )

    async def delete_redirect_rule(self, zone_id: str, ruleset_id: str, rule_id: str) -> None:
        await self.request("DELETE", f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{rule_id}")

    async def list_page_rules(self, zone_id: str) -> list[dict]:
        """Legacy Page Rules. Read to *detect a conflict*, never to write one — a forwarding
        Page Rule and our redirect rule on the same hostname is exactly the "it already
        redirects, but not through us" case."""
        return await self.request("GET", f"/zones/{zone_id}/pagerules") or []

    # --- Pages ----------------------------------------------------------------------------- #
    async def list_pages_projects(self, account_id: str) -> list[dict]:
        return await self.paginate(f"/accounts/{account_id}/pages/projects")

    async def list_pages_domains(self, account_id: str, project: str) -> list[dict]:
        return (
            await self.request(
                "GET", f"/accounts/{account_id}/pages/projects/{project}/domains"
            )
            or []
        )

    async def add_pages_domain(self, account_id: str, project: str, hostname: str) -> dict:
        return (
            await self.request(
                "POST",
                f"/accounts/{account_id}/pages/projects/{project}/domains",
                json={"name": hostname},
            )
            or {}
        )

    async def delete_pages_domain(self, account_id: str, project: str, hostname: str) -> None:
        await self.request(
            "DELETE", f"/accounts/{account_id}/pages/projects/{project}/domains/{hostname}"
        )
