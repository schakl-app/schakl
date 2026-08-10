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
#: calls. Deliberately short: an *edit* scope cannot be observed at all without writing
#: something, and this module will not create a record on a client's zone to find out.
#:
#: The last two need a zone in hand, which is why they are probed only when the caller has one
#: (``verify_account`` picks a synced zone of this account). They were the conspicuous hole:
#: **the two scopes the redirect button actually uses were the two the screen never mentioned**,
#: so an admin whose "Wat dit token mag" list read ✓ all the way down still got a token error at
#: the button — which is exactly what "the token seems to have the right permissions" means.
#: They are *read* probes, and the module is honest about that in the label: a token that cannot
#: read a zone's DNS certainly cannot write it, while one that can read it may still not write.
CAPABILITIES: tuple[str, ...] = (
    "token_valid",
    "accounts_read",
    "zones_read",
    "pages_read",
    "registrar_read",
    "dns_read",
    "redirect_read",
)

#: The subset of :data:`CAPABILITIES` that cannot be asked without a zone to address. They are
#: **omitted from the answer** rather than reported ``False`` when no zone was available, because
#: "we did not look" and "not granted" are different answers (docs/CLOUDFLARE.md §6) and a red
#: "niet toegekend" against a scope nobody asked about is the same lie in the other direction.
ZONE_CAPABILITIES: frozenset[str] = frozenset({"dns_read", "redirect_read"})

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


def describe_failure(exc: CloudflareError) -> str:
    """A refusal as one line an admin can act on: the status, the code, and Cloudflare's own text.

    The status and the code are *the* diagnosis and neither is in ``str(exc)``. "Actor is not
    authorized" is a scope to add; a 400 naming a query parameter is our bug; ``9109`` is an IP
    filter and nothing to do with either. A probe that recorded only the message would keep the
    least useful third of what Cloudflare said.
    """
    head = " ".join(
        part
        for part in (
            f"HTTP {exc.status}" if exc.status is not None else "",
            f"(code {exc.code})" if exc.code is not None else "",
        )
        if part
    )
    text = str(exc).strip()
    if not head:
        return text[:200]
    return (f"{head}: {text}" if text else head)[:200]


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


def _rejects_list_options(exc: CloudflareError) -> bool:
    """Whether Cloudflare refused the *pagination*, rather than the call.

    ``400 Invalid list options provided. Review the `page` or `per_page` parameter.`` is what a
    single-page endpoint answers to a paged request. Matched on the text, because the numeric
    code differs per product while the sentence does not, and because the neighbouring shape —
    ``per_page must be between 5 and 100`` — deserves the same answer: both say the query
    parameters were the problem, and both are cured by asking without them.

    Deliberately narrow. A ``400`` that names neither is about the request itself and must still
    fail: retrying it plainly would turn one honest error into two.
    """
    if exc.status != 400:
        return False
    text = str(exc).lower()
    return "list options" in text or "per_page" in text


def _is_last_page(result: list[Any], info: dict, size: int = PER_PAGE) -> bool:
    """Whether Cloudflare has anything after the page just read.

    ``result_info`` answers it outright where Cloudflare sends one, and that is worth preferring:
    an endpoint that *accepts* ``per_page`` and then ignores it answers every page identically,
    so a row count alone would ask twenty times for the same rows and end in the cap's error.
    The count stays as the fallback for the endpoints that report nothing — measured against
    ``size``, which is *ours* on the ordinary path and **Cloudflare's own** when the endpoint
    picked the size itself (:meth:`CloudflareClient._read_rest`). A short page is only evidence
    of the end against the size that page was actually served at.
    """
    page = info.get("page")
    total_pages = info.get("total_pages")
    if isinstance(page, int) and isinstance(total_pages, int) and total_pages >= 1:
        return page >= total_pages
    return len(result) < size


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
        result, _info = await self._send_envelope(http, method, path, json=json, params=params)
        return result

    async def _send_envelope(
        self,
        http: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, dict]:
        """The ``result``, plus the ``result_info`` it arrived with (``{}`` when there is none).

        Only :meth:`paginate` wants the second half, and it wants it for one reason: Cloudflare's
        own count of what it is holding is the only way to tell a last page from a truncated one.
        """
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

    def _unwrap(self, response: httpx.Response) -> tuple[Any, dict]:
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
        info = body.get("result_info")
        return body.get("result"), info if isinstance(info, dict) else {}

    async def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Every page of a list endpoint, capped at :data:`MAX_PAGES` (never truncated silently
        — hitting the cap raises, per CLAUDE.md §17's "over the limit is an error").

        **Not every list endpoint takes a page.** Cloudflare's own SDK types a handful of them —
        Registrar's domains, several account-level lists — as *single page*: they answer the whole
        collection at once and reject ``page``/``per_page`` outright with
        ``400 Invalid list options provided``. Asking for a page of one of those failed the read
        completely, which on a sync surfaced as *"Niet alles kon gelezen worden"* over a token
        that was scoped for it and an account Cloudflare was perfectly willing to describe.

        So a refusal that names the list options is not fatal: the same path is asked **plainly**,
        once, and what comes back is read for what it is — the whole collection where the endpoint
        has no pages, or Cloudflare's *own* page where it merely declined the size asked for
        (:meth:`_read_whole`). Either way this never returns a prefix as a whole list, which is
        the one outcome worse than an error (§17).
        """
        rows: list[dict] = []
        async with self._http() as http:
            for page in range(1, MAX_PAGES + 1):
                query = {**(params or {}), "page": page, "per_page": PER_PAGE}
                try:
                    result, info = await self._send_envelope(http, "GET", path, params=query)
                except CloudflareError as exc:
                    if page > 1 or not _rejects_list_options(exc):
                        raise
                    return await self._read_whole(http, path, params)
                result = result or []
                rows.extend(r for r in result if isinstance(r, dict))
                if _is_last_page(result, info, PER_PAGE):
                    return rows
        raise CloudflareError(
            f"Cloudflare returned more than {MAX_PAGES * PER_PAGE} rows for {path}"
        )

    async def _read_whole(
        self, http: httpx.AsyncClient, path: str, params: dict[str, Any] | None
    ) -> list[dict]:
        """The plain read of a list endpoint that refused our page parameters — and, where that
        answer turns out to be a page of Cloudflare's own choosing, the rest of it.

        A refusal reads like *"this endpoint has no pages"*, and for Registrar's domains that is
        exactly what it is. For Pages' projects it is not: that list pages perfectly well and
        merely declines the **size** it was asked for, so the plain read came back holding ten of
        thirteen projects and said so in ``result_info`` — and calling that a failed read turned a
        quarrel about ``per_page`` into *"Niet alles kon gelezen worden"* over three Pages
        projects nobody could see. Refusing a prefix (§17) was right; stopping at one was not.

        So the plain answer is taken at its word. When Cloudflare says it is holding more than it
        handed over it has just *described the page it served* — its own size, its own numbering —
        and the read continues from page two. Completeness is then the ordinary loop's business,
        exactly as on the paged path: ``result_info`` says which page is the last one.

        Only when there is no next page to ask for — no page one to build on, or the resume
        refused too — is this the failure it always was, and it still names the prefix it declined
        to return.
        """
        result, info = await self._send_envelope(http, "GET", path, params=params)
        rows = [r for r in (result or []) if isinstance(r, dict)]
        total = info.get("total_count")
        if not isinstance(total, int) or total <= len(rows):
            return rows
        if rows:
            try:
                return await self._read_rest(http, path, params, rows, info)
            except CloudflareError as exc:
                if not _rejects_list_options(exc):
                    raise
        raise CloudflareError(
            f"Cloudflare refused page parameters for {path} and then answered "
            f"{len(rows)} of {total} rows"
        )

    async def _read_rest(
        self,
        http: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None,
        first: list[dict],
        info: dict,
    ) -> list[dict]:
        """Page two onwards of an endpoint that served page one without being asked.

        It is handed ``page`` and **nothing else**. The size is Cloudflare's to choose and it has
        already chosen — ``result_info`` names it, and what it actually handed over is the
        fallback — so re-sending a ``per_page`` would only re-open the argument that got us here.
        """
        size = info.get("per_page")
        if not isinstance(size, int) or size <= 0:
            size = len(first)
        rows = list(first)
        for page in range(2, MAX_PAGES + 1):
            result, info = await self._send_envelope(
                http, "GET", path, params={**(params or {}), "page": page}
            )
            result = result or []
            rows.extend(r for r in result if isinstance(r, dict))
            if _is_last_page(result, info, size):
                return rows
        raise CloudflareError(f"Cloudflare returned more than {MAX_PAGES * size} rows for {path}")

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
        self, account_id: str | None = None, zone_id: str | None = None
    ) -> tuple[dict[str, bool], dict | None, dict[str, str]]:
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

        **Every refusal is kept** (the third return value, capability → :func:`describe_failure`).
        Failing softly used to mean discarding the answer: a probe that ended ``False`` said so
        with no status, no code and no text, anywhere — not on the row, not in a log — so a ✗
        against a token whose Cloudflare screen plainly grants that permission left nothing to
        diagnose it with, and the only remaining move was to widen a token that was already wide
        enough. Soft is about not raising, not about not remembering.
        """
        caps = dict.fromkeys((c for c in CAPABILITIES if c not in ZONE_CAPABILITIES), False)
        errors: dict[str, str] = {}
        rejection: CloudflareAuthError | None = None

        # Accounts first, because the account-owned verify endpoint needs an id to address.
        account: dict | None = None
        try:
            accounts = await self.list_accounts()
        except CloudflareAuthError as exc:
            rejection = exc
            errors["accounts_read"] = describe_failure(exc)
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
            errors["token_valid"] = describe_failure(exc)
        else:
            caps["token_valid"] = True

        # **No `per_page` on any of these three, for the reason the zone probes shed theirs**: a
        # probe must differ as little as possible from the call it stands in for, and a page size
        # is exactly the kind of difference that answers a question nobody asked — Registrar
        # refuses list options outright, so `per_page=1` made "may this token read the register?"
        # depend on a parameter the register does not have. Asking plainly costs one page once.
        try:
            await self.request("GET", "/zones")
        except CloudflareAuthError as exc:
            rejection = rejection or exc
            errors["zones_read"] = describe_failure(exc)
        except CloudflareError as exc:
            # Not every refusal here is about the token, and one that is not must still land in
            # `errors` rather than escaping: this probe was the only one in the list that let a
            # non-auth failure out, so a 400 from `/zones` failed the whole verify screen instead
            # of marking one capability — the screen exists precisely to degrade per probe.
            errors["zones_read"] = describe_failure(exc)
        else:
            caps["zones_read"] = True

        if account_id:
            try:
                await self.request("GET", f"/accounts/{account_id}/pages/projects")
            except CloudflareAuthError as exc:
                errors["pages_read"] = describe_failure(exc)
            except CloudflareError as exc:
                # Pages not enabled on the account answers 4xx, not 403 — same conclusion, and
                # the two are worth telling apart on the screen, which is what the text is for.
                errors["pages_read"] = describe_failure(exc)
            else:
                caps["pages_read"] = True

            try:
                await self.request("GET", f"/accounts/{account_id}/registrar/domains")
            except CloudflareError as exc:
                # Registrar is its own token permission (#298) and an account that has never
                # registered anything through Cloudflare may answer 4xx outright. Either way
                # the conclusion is the same: this token is not evidence about registrations.
                errors["registrar_read"] = describe_failure(exc)
            else:
                caps["registrar_read"] = True

        if zone_id:
            # The two scopes the domain page's own buttons use, and the two that were invisible
            # until the button failed. Both are reads, and a read is only half the answer — but
            # it is the half that catches "this permission was never granted", which is the case
            # that produced a token error against a token whose every other line said ✓.
            for capability, path in (
                ("dns_read", f"/zones/{zone_id}/dns_records"),
                ("redirect_read", f"/zones/{zone_id}/rulesets/phases/{REDIRECT_PHASE}/entrypoint"),
            ):
                # Present from here on, because a zone *was* available: from this point "False"
                # is an answer. Absent stays reserved for the case above, where nothing was asked.
                caps[capability] = False
                try:
                    # **No query parameters.** A probe must differ from the call it stands in
                    # for as little as possible, and ``per_page=1`` was the only thing here that
                    # no real call does (``paginate`` sends 50). Cloudflare's current schema
                    # documents ``minimum: 1``, so it *should* be accepted — but the retired
                    # reference documented a minimum of 5 for this very endpoint, "should" is
                    # not evidence, and a probe is the wrong place to spend a difference that
                    # buys nothing. Asking plainly costs one page of records once per verify.
                    await self.request("GET", path)
                except CloudflareAuthError as exc:
                    rejection = rejection or exc
                    errors[capability] = describe_failure(exc)
                except CloudflareError as exc:
                    # A zone with no redirect rules has no entrypoint ruleset and answers 404
                    # (:meth:`get_redirect_ruleset`) — a normal state, and the token was plainly
                    # allowed to ask. Every *other* non-auth failure (Cloudflare unreachable, a
                    # 5xx) is evidence about Cloudflare and none about the scope, so it leaves
                    # the answer False rather than inventing a ✓ — and, below, leaves the
                    # "every probe refused" check able to fire.
                    caps[capability] = exc.status == 404
                    if not caps[capability]:
                        errors[capability] = describe_failure(exc)
                else:
                    caps[capability] = True

        if not any(caps.values()):
            # Every probe refused. Now — and only now — "the token is invalid" is a statement
            # about the token rather than about one endpoint's opinion of it.
            raise rejection or CloudflareAuthError("Cloudflare rejected the token", status=401)
        if not caps["token_valid"]:
            # Something answered, so Cloudflare accepts this token; we just could not reach a
            # verify endpoint that would say so out loud. The reads are better evidence anyway
            # — they are the calls the module makes.
            caps["token_valid"] = True
        # A capability that ended True has nothing to explain, and ``token_valid`` reaches here
        # having been overruled by better evidence: keeping its refusal would print a failure
        # beside a ✓.
        return caps, account, {k: v for k, v in errors.items() if not caps.get(k)}

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
