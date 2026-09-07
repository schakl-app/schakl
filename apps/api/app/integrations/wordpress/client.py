"""WordPress REST client, per **website** credential (docs/WORDPRESS.md). Business-licensed.

One WordPress Application Password reaches four surfaces on the same host, which is the whole
reason this module exists as one credential rather than two integrations:

===========================================  ==================================================
``/wp-json/wp/v2/*``                          posts, media, users — core REST
``/wp-json/wp-abilities/v1/*``                WordPress 6.9's Abilities API
``/wp-json/mcp/<server>``                     the MCP Adapter, if installed
``/wp-json/rankmath/v1/ai-visibility/*``      Rank Math's AI Visibility proxy
===========================================  ==================================================

Rules that do not bend:

* **The password never reaches a log line, an exception message, or a response.** It lives in
  one ``Authorization`` header. :class:`WordPressError` carries the site's own error text and
  nothing else, and :func:`describe_failure` is what a probe stores.
* **A probe is evidence, never the gate** (``cloudflare``'s rule, and the one this module would
  most easily break). Rank Math absent on a site whose posts API is perfectly healthy is an
  ordinary state, and so is MCP absent where Rank Math works. Every probe fails softly, keeps
  its own refusal, and only a credential refused by **every** probe is called invalid.
* **The ability is not the REST route.** ``rank-math/get-ai-visibility-overview`` reads a
  12-hour ``wp_options`` cache and cannot force an upstream fetch — its own ``refresh`` input
  is telemetry only, verified in the plugin source (1.0.275,
  ``includes/abilities/ai-visibility/class-get-ai-visibility-overview.php``). Only the REST
  controller reaches Rank Math's backend, so :meth:`ai_visibility_overview` is what a *sync*
  calls and the ability is for a conversational reader who should be reading a cache.
* **The network is off in tests.** :data:`_transport` is the only seam; unset, every call goes
  to the real host and a test that forgot to stub fails loudly on connect.

Everything here is written from the plugin source and the official WordPress documentation, and
**has never been exercised against a live site with a Content AI subscription** — so every
parse is defensive and ``docs/WORDPRESS.md`` §1 carries the checklist to run the day one
arrives (the posture ``docs/OXXA.md`` takes, for the same reason).
"""

from __future__ import annotations

import base64
import json as _json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.net_guard import SsrfBlocked, assert_host_public_sync

logger = logging.getLogger("schakl.wordpress")

#: A client's WordPress is a dependency of a *screen*, not of a page load. Generous enough for
#: a cold PHP host, bounded so a hung request inside a cron never holds a worker slot.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)

#: The REST index, which is also the cheapest thing on a WordPress site that proves it *is* one.
REST_ROOT = "/wp-json/"
#: WordPress 6.9 core. Individual abilities opt in with ``show_in_rest``; Rank Math's do.
ABILITIES_PATH = "/wp-json/wp-abilities/v1/abilities"
#: Rank Math's own cache-backed proxy — ``Rest_Helper::BASE`` is ``rankmath/v1``.
AIV_BASE = "/wp-json/rankmath/v1/ai-visibility"
#: Core's plugin list. Administrator-only (``activate_plugins``), which is fine because every
#: Rank Math AI Visibility route needs ``manage_options`` anyway — and it is the **only**
#: reliable way to read a plugin's version over REST. There is deliberately no probe for the
#: WordPress *core* version: core does not publish it over REST, and the honest signal for
#: "new enough for the Abilities API" is whether the ``wp-abilities/v1`` namespace is in the
#: index at all, which is what the ``abilities`` capability already reports. A guessed version
#: string would be a stored fact nobody observed.
PLUGINS_PATH = "/wp-json/wp/v2/plugins"

#: Rank Math's plugin file, as ``wp/v2/plugins`` keys it.
RANKMATH_PLUGIN_PREFIX = "seo-by-rank-math/"

#: The MCP Adapter's REST **namespace**. The servers are *routes* under it —
#: ``/mcp/mcp-adapter-default-server`` by default, one more per server a plugin creates — which
#: is why the server path is discovered from the index's ``routes`` and never hardcoded
#: (CLAUDE.md §12). The first cut of this probe matched ``n.startswith("mcp/")`` against the
#: namespace list, and the namespace is the bare ``mcp``: every connected site, adapter
#: installed and answering, was recorded as ``no_mcp_namespace`` — found by reading three live
#: sites' indexes rather than the module's own fake, which had been taught the same wrong shape.
MCP_NAMESPACE = "mcp"
#: The MCP Adapter's index route (``/mcp``); a server is anything one segment deeper.
MCP_ROUTE_PREFIX = "/mcp/"

#: Core content: ``/wp-json/wp/v2/<rest_base>``. Which ``rest_base`` a post type answers on is
#: the site's own statement (``/wp/v2/types``), never derived from the type's slug — a custom
#: post type registered as ``dienst`` may answer on ``diensten``.
CONTENT_BASE = "/wp-json/wp/v2"
#: Contact Form 7's own namespace. Its update takes the **flat** ``wpcf7_save_contact_form``
#: shape (``form`` a string, ``mail`` a dict, ``messages`` a dict), while its read answers a
#: nested ``properties`` — read from the plugin source (``includes/rest-api.php``,
#: ``contact-form-functions.php``), because the two are not symmetric and a body posted in the
#: read's shape is silently ignored.
CF7_BASE = "/wp-json/contact-form-7/v1/contact-forms"
#: The two core abilities WordPress 6.9 registers read-only, ``show_in_rest``. Together they say
#: what core's REST never does on its own: the WordPress and PHP versions.
CORE_SITE_INFO = "core/get-site-info"
CORE_ENVIRONMENT_INFO = "core/get-environment-info"

#: The Rank Math release that introduced AI Visibility. Below it the plugin is installed and the
#: feature is simply not there — a different sentence from "not installed", and one the panel
#: says out loud rather than showing an empty integration.
RANKMATH_AIV_MIN_VERSION = (1, 0, 273)

#: What :func:`probe_capabilities` can observe with a handful of cheap reads. Deliberately short
#: and deliberately all *reads*: this module will not publish a draft post on a client's live
#: site to find out whether it may write.
CAPABILITIES: tuple[str, ...] = (
    "rest",
    "admin",
    "abilities",
    "rankmath_aiv",
    "mcp",
)


class WordPressError(RuntimeError):
    """A call failed. ``message`` is the site's own error text — never the credential."""

    def __init__(
        self, message: str, *, status: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        #: WordPress's own error slug (``rest_forbidden``, ``rest_no_route``,
        #: ``aiv_unauthorized``) — the only reliable way to tell "this route does not exist"
        #: from "you may not call it", which are opposite diagnoses that share a 4xx.
        self.code = code


class WordPressAuthError(WordPressError):
    """The credential was refused. Retrying cannot help; only the tenant can fix it."""


class WordPressUnreachable(WordPressError):
    """We never got an answer: DNS, TLS, a timeout, a blocked address.

    Its own class because it says **nothing about the credential**. Reporting it as an auth
    failure sends an admin to re-mint an application password that was never wrong, which is
    the mistake ``uptime``'s ``NEEDS_REAUTH`` split exists to prevent one layer over.
    """


def describe_failure(exc: Exception) -> str:
    """One line an admin can act on: the status, the site's error slug, and its own text.

    The slug is the diagnosis and is not in ``str(exc)``. ``rest_no_route`` means the plugin is
    not there; ``rest_forbidden`` means this user is not an administrator; ``aiv_unauthorized``
    means Rank Math itself is not connected to a Content AI subscription. A probe that stored
    only the message would keep the least useful third of what the site said.
    """
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    head = " ".join(
        part
        for part in (
            f"HTTP {status}" if status is not None else "",
            f"({code})" if code else "",
        )
        if part
    )
    text = str(exc).strip()
    if not head:
        return text[:200]
    return (f"{head}: {text}" if text else head)[:200]


def normalise_base_url(raw: str) -> str:
    """A site URL as this module stores it: scheme, host, optional subpath, no trailing slash.

    A stored ``https://klant.nl/`` and ``https://klant.nl`` must be the same site, or the panel
    shows two credentials for one WordPress and the unique index never fires.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower() if parsed.scheme in ("http", "https") else "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def _version_tuple(raw: str | None) -> tuple[int, ...]:
    """``"1.0.275"`` → ``(1, 0, 275)``. A version we cannot parse sorts as ancient, so an
    unrecognised string reads "too old" rather than silently passing the minimum check."""
    parts: list[int] = []
    for chunk in (raw or "").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def supports_ai_visibility(rankmath_version: str | None) -> bool:
    """Whether this Rank Math is new enough to have AI Visibility at all."""
    version = _version_tuple(rankmath_version)
    return bool(version) and version >= RANKMATH_AIV_MIN_VERSION


#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every client uses. Tests only."""
    global _transport
    _transport = transport


def _error_from_body(body: Any, status: int) -> tuple[str, str | None]:
    """WordPress's error envelope → (text, slug). Defensive: a 502 from a reverse proxy is an
    HTML page, a WAF block is neither JSON nor WordPress, and a PHP fatal is plain text.

    An **empty** text where the body says nothing, deliberately. :func:`describe_failure`
    prefixes the status itself, so returning ``"HTTP 404"`` here rendered as *"HTTP 404: HTTP
    404"* on the panel — noise dressed as diagnosis, which is worse than the bare cross it was
    meant to replace.
    """
    if isinstance(body, dict):
        message = body.get("message")
        code = body.get("code")
        return (
            str(message) if isinstance(message, str) and message else "",
            str(code) if isinstance(code, str) and code else None,
        )
    return "", None


class WordPressClient:
    """One site's worth of WordPress access. Cheap to construct, one per operation."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        *,
        allow_private: bool | None = None,
    ) -> None:
        self.base_url = normalise_base_url(base_url)
        self._username = username
        self._password = app_password
        self._allow_private = allow_private

    # --- transport ------------------------------------------------------------------------ #
    def _auth_header(self) -> str:
        """Basic auth, which is how WordPress Application Passwords authenticate.

        WordPress strips the spaces from the password it displays; a user pasting it verbatim
        is the normal case and both forms authenticate, so we send what we were given.
        """
        raw = f"{self._username}:{self._password}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_TIMEOUT,
            headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
                # Some managed hosts answer a bare client with a challenge page; naming
                # ourselves is also what a client's admin sees in their access log.
                "User-Agent": "schakl/1.0 (+https://schakl.app)",
            },
            # A WordPress site redirecting http→https, or apex→www, is entirely ordinary.
            follow_redirects=True,
            transport=_transport,
        )

    def _guard(self) -> None:
        """Refuse a target that resolves to a non-public address, unless this install opted in.

        Deliberately the *overridable* guard rather than a hard block: an agency's own staging
        WordPress on the LAN is an ordinary thing for a self-hoster to point this at, and
        ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_TARGETS`` is the switch that already exists for
        exactly that trade-off (``app/core/net_guard.py``).
        """
        host = urlparse(self.base_url).hostname or ""
        assert_host_public_sync(host, allow_private=self._allow_private)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """One authenticated call, returning the decoded body."""
        async with self._http() as http:
            return await self._send(http, method, path, params=params, json=json)

    async def request_page(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, int | None]:
        """One authenticated list read, returning ``(body, total)``.

        WordPress puts the collection size in ``X-WP-Total`` rather than in the body, and a
        list that does not say how much of it was shown is a prefix passed off as the answer
        (§17). ``None`` where the site did not say.
        """
        async with self._http() as http:
            body, headers = await self._send(http, "GET", path, params=params, with_headers=True)
        raw = headers.get("x-wp-total")
        try:
            total = int(raw) if raw is not None else None
        except ValueError:
            total = None
        return body, total

    async def request_full(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> tuple[Any, int | None]:
        """Any verb, returning ``(body, total)`` — the passthrough's one call."""
        async with self._http() as http:
            body, headers = await self._send(
                http, method, path, params=params, json=json, with_headers=True
            )
        raw = headers.get("x-wp-total")
        try:
            total = int(raw) if raw is not None else None
        except ValueError:
            total = None
        return body, total

    async def _send(
        self,
        http: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        with_headers: bool = False,
    ) -> Any:
        if _transport is None:
            # Only when we are really going out: a MockTransport target need not be routable,
            # and running the guard against it would make every test assert DNS.
            self._guard()
        try:
            response = await http.request(method, path, params=params, json=json)
        except SsrfBlocked as exc:
            raise WordPressUnreachable(str(exc)) from exc
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error names the URL, never a header — the credential cannot
            # travel this way.
            raise WordPressUnreachable(str(exc) or exc.__class__.__name__) from exc

        try:
            body: Any = response.json()
        except ValueError:
            body = None

        if response.is_success:
            return (body, response.headers) if with_headers else body

        text, code = _error_from_body(body, response.status_code)
        if response.status_code in (401, 403):
            raise WordPressAuthError(text, status=response.status_code, code=code)
        raise WordPressError(text, status=response.status_code, code=code)

    # --- the four surfaces ---------------------------------------------------------------- #
    async def rest_index(self) -> dict[str, Any]:
        """The site's REST index: its name, its namespaces, and therefore what it has."""
        body = await self.request("GET", REST_ROOT)
        return body if isinstance(body, dict) else {}

    async def current_user(self) -> dict[str, Any]:
        """Who this credential is, with ``capabilities`` — ``context=edit`` is what includes
        them, and they are the only way to know whether ``manage_options`` holds without
        guessing from a role name a plugin may have redefined."""
        body = await self.request("GET", "/wp-json/wp/v2/users/me", params={"context": "edit"})
        return body if isinstance(body, dict) else {}

    async def abilities(self) -> list[dict[str, Any]]:
        """Every ability this user may see. Rank Math's four AI Visibility ones are in here on
        a site running 6.9 + Rank Math ≥ 1.0.273, because it registers them ``show_in_rest``."""
        rows: list[dict[str, Any]] = []
        # Core paginates the list (`per_page` ≤ 100) and says how many there are in
        # `X-WP-Total`; ACF 6.8 alone registers a dozen per post type, so one page of the
        # default size is a prefix. Bounded, because a site that claims ten thousand abilities
        # is not one to keep asking.
        for page in range(1, 11):
            body, total = await self.request_page(
                ABILITIES_PATH, params={"per_page": 100, "page": page}
            )
            chunk = _ability_rows(body)
            rows.extend(chunk)
            if len(chunk) < 100 or (total is not None and len(rows) >= total):
                break
        return rows

    async def ability(self, name: str) -> dict[str, Any]:
        """One ability's registration: label, schemas, annotations."""
        body = await self.request("GET", f"{ABILITIES_PATH}/{name}")
        return body if isinstance(body, dict) else {}

    async def run_ability(
        self, name: str, annotations: dict[str, Any], input: Any = None
    ) -> Any:
        """Execute an ability the way core insists it be executed.

        The verb is the ability's own claim about itself (``class-wp-rest-abilities-v1-run-
        controller.php``): ``readonly`` → ``GET``, ``destructive`` + ``idempotent`` → ``DELETE``,
        anything else → ``POST``. Sending the wrong one is a 405, so the caller passes the
        annotations it read off the listing rather than guessing. ``GET``/``DELETE`` carry the
        input as ``input[key]=value`` query parameters, which is what ``get_query_params()``
        reads and what 7.1's schema coercion turns back into typed values; ``POST`` carries it
        as ``{"input": …}`` in the body.
        """
        verb = "POST"
        if annotations.get("readonly"):
            verb = "GET"
        elif annotations.get("destructive") and annotations.get("idempotent"):
            verb = "DELETE"
        path = f"{ABILITIES_PATH}/{name}/run"
        if verb == "POST":
            return await self.request("POST", path, json={"input": input})
        return await self.request(verb, path, params=_query_input(input))

    # --- core content --------------------------------------------------------------------- #
    async def content_types(self) -> dict[str, dict[str, Any]]:
        """Every post type this user may edit, keyed by slug, with its ``rest_base``."""
        body = await self.request(
            "GET", f"{CONTENT_BASE}/types", params={"context": "edit"}
        )
        if not isinstance(body, dict):
            return {}
        return {k: v for k, v in body.items() if isinstance(v, dict)}

    async def list_content(
        self, rest_base: str, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int | None]:
        """A page of one post type, ``context=edit`` so drafts and raw content are in it."""
        body, total = await self.request_page(
            f"{CONTENT_BASE}/{rest_base}", params={"context": "edit"} | params
        )
        rows = [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []
        return rows, total

    async def get_content(self, rest_base: str, wp_id: int) -> dict[str, Any]:
        body = await self.request(
            "GET", f"{CONTENT_BASE}/{rest_base}/{wp_id}", params={"context": "edit"}
        )
        return body if isinstance(body, dict) else {}

    async def write_content(
        self, rest_base: str, wp_id: int | None, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Create (``wp_id`` None) or update one record. Core's update is a ``POST`` too."""
        path = f"{CONTENT_BASE}/{rest_base}" + (f"/{wp_id}" if wp_id is not None else "")
        answer = await self.request("POST", path, params={"context": "edit"}, json=body)
        return answer if isinstance(answer, dict) else {}

    async def list_media(self, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
        body, total = await self.request_page(f"{CONTENT_BASE}/media", params=params)
        rows = [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []
        return rows, total

    async def get_media(self, wp_id: int) -> dict[str, Any]:
        body = await self.request("GET", f"{CONTENT_BASE}/media/{wp_id}")
        return body if isinstance(body, dict) else {}

    # --- Contact Form 7 ------------------------------------------------------------------- #
    async def list_forms(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        body = await self.request("GET", CF7_BASE, params=params)
        return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []

    async def get_form(self, wp_id: int) -> dict[str, Any]:
        body = await self.request("GET", f"{CF7_BASE}/{wp_id}")
        return body if isinstance(body, dict) else {}

    async def form_schema(self, wp_id: int) -> dict[str, Any]:
        """The validation rules a submission is judged by — the closest thing CF7 has to a
        field list, and public on the site, so it costs the credential nothing."""
        body = await self.request("GET", f"{CF7_BASE}/{wp_id}/feedback/schema")
        return body if isinstance(body, dict) else {}

    async def write_form(self, wp_id: int | None, body: dict[str, Any]) -> dict[str, Any]:
        """Create (``wp_id`` None) or update a form, in ``wpcf7_save_contact_form``'s flat shape."""
        path = CF7_BASE + (f"/{wp_id}" if wp_id is not None else "")
        answer = await self.request("POST", path, json=body)
        return answer if isinstance(answer, dict) else {}

    async def ai_visibility_overview(self, *, refresh: bool = False) -> dict[str, Any]:
        """Rank Math's AI Visibility dashboard payload: ``{summary, brands[]}``.

        ``refresh=True`` is what a **sync** must pass, and the reason is worth restating at the
        call site because the alternative looks more modern and is wrong: the controller serves
        its 12-hour ``wp_options`` cache unless forced, and the *ability* cannot force it at
        all. Without this flag schakl would chart a number that moves only when somebody opens
        the WordPress dashboard.

        The envelope is Rank Math's own ``{success, data}``; the payload is under ``data``.
        """
        body = await self.request(
            "GET", f"{AIV_BASE}/overview", params={"refresh": "1"} if refresh else None
        )
        return _unwrap(body)

    async def ai_visibility_insights(self, brand_id: str) -> dict[str, Any]:
        """One brand's latest analysis: competitors, per-query results, transcripts."""
        body = await self.request("GET", f"{AIV_BASE}/brands/{brand_id}/insights")
        return _unwrap(body)

    async def ai_visibility_queries(self, brand_id: str) -> dict[str, Any]:
        """The prompts a brand is tracked on."""
        body = await self.request("GET", f"{AIV_BASE}/brands/{brand_id}/queries")
        return _unwrap(body)

    # --- the probe ------------------------------------------------------------------------ #
    async def probe_capabilities(self) -> tuple[dict[str, bool], dict[str, str], dict[str, Any]]:
        """What this credential reaches, why each refusal happened, and what the site is.

        Five independent probes, none of which gates another, because the states they describe
        are independent in reality: Rank Math is routinely absent from a site whose posts API is
        perfectly healthy, the MCP Adapter is routinely absent from a site where Rank Math
        works, and a credential belonging to an editor rather than an administrator reaches
        ``wp/v2`` and nothing else. A verify that ran one check and raised for everything behind
        it would convert one endpoint's opinion into a verdict on the whole integration — the
        mistake ``docs/CLOUDFLARE.md`` records, and the one this module is most exposed to
        because it has *four* surfaces rather than one.

        **Every refusal is kept.** Failing softly is about not raising, not about not
        remembering: a ✗ with no status, no slug and no text leaves an admin nothing to act on,
        and the two failures that look identical from outside — a host that strips the
        ``Authorization`` header, and an application password that was revoked — are told apart
        by nothing else.

        Returns ``(capabilities, capability_errors, observed)``. The caller decides what to do
        with a credential every probe refused; see
        :func:`app.integrations.wordpress.service.credential_rejected`.
        """
        # Starts **empty**, and a key appears only once that surface was actually asked. A
        # pre-filled `dict.fromkeys(CAPABILITIES, False)` looks harmless and destroys the
        # distinction this module is built on: every capability would read "probed and refused"
        # even where nothing was ever asked. Two of the five are only *knowable* through
        # another probe's answer — `admin` lives in the `users/me` body and `mcp` in the REST
        # index's namespace list — so when those fail, the honest answer is a missing key,
        # which the panel draws as "niet gecontroleerd" rather than a red cross it cannot
        # explain.
        caps: dict[str, bool] = {}
        errors: dict[str, str] = {}
        observed: dict[str, Any] = {}

        async with self._http() as http:

            async def probe(name: str, coro_path: str, **kwargs: Any) -> Any:
                try:
                    return await self._send(http, "GET", coro_path, **kwargs)
                except (WordPressError, WordPressUnreachable) as exc:
                    errors[name] = describe_failure(exc)
                    return None

            # 1. The REST index. Also the site's self-description, so it feeds three fields.
            index = await probe("rest", REST_ROOT)
            if isinstance(index, dict):
                namespaces = index.get("namespaces")
                namespaces = [n for n in namespaces if isinstance(n, str)] if isinstance(
                    namespaces, list
                ) else []
                # Discovered, never assumed: the namespace is `mcp` and each server is a route
                # under it. A site with several servers (breik.nl carries an OAuth one beside
                # the default) records the first that answers `POST`, which is the transport.
                mcp = mcp_server_route(namespaces, index.get("routes"))
                # Only decidable *because* the index answered — hence set here and nowhere
                # else. An index that failed leaves `mcp` absent, not False.
                caps["mcp"] = bool(mcp)
                if mcp:
                    observed["mcp_server_path"] = f"/wp-json{mcp}"
                else:
                    errors["mcp"] = "no_mcp_namespace"
                if not any(n.startswith("rankmath/") for n in namespaces):
                    observed["rankmath_absent"] = True

            # 2. Who we are. This is the probe that says the credential itself works, so a
            #    refusal here is the one worth reporting hardest.
            me = await probe("rest", "/wp-json/wp/v2/users/me", params={"context": "edit"})
            caps["rest"] = isinstance(me, dict)
            if isinstance(me, dict):
                errors.pop("rest", None)
                observed["username"] = me.get("slug") or me.get("name")
                capabilities = me.get("capabilities")
                # Same rule as `mcp`: `admin` lives inside this body, so it is knowable only
                # when this body arrived.
                caps["admin"] = bool(
                    isinstance(capabilities, dict) and capabilities.get("manage_options")
                )
                if not caps["admin"]:
                    # Not a transport failure, so it never came through `probe`. Every Rank
                    # Math AI Visibility route is `manage_options`, so this is the difference
                    # between a working integration and a 403 at every call.
                    errors["admin"] = "not_administrator"

            # 3. Abilities (WP 6.9 core). Their absence is a WordPress version, not a fault.
            abilities = await probe("abilities", ABILITIES_PATH)
            caps["abilities"] = isinstance(abilities, list | dict)
            if caps["abilities"]:
                errors.pop("abilities", None)

            # 4. Rank Math's version, from core's plugin list. **Not a capability**: it answers
            #    "how new is the plugin", which is a different question from "may this
            #    credential reach it", and 1.0.273 is where AI Visibility begins.
            #
            #    A refusal here records nothing at all — not an error, and emphatically not
            #    `rankmath_absent`. This is the "a probe that ran and found nothing clears its
            #    own entry; one that could not run leaves the previous value alone" rule
            #    (docs/CLOUDFLARE.md), and getting it backwards would have a non-admin
            #    credential silently delete the version we learned from an admin one.
            try:
                plugins = await self._send(http, "GET", PLUGINS_PATH)
            except (WordPressError, WordPressUnreachable):
                plugins = None
            if isinstance(plugins, list):
                version = _rankmath_version(plugins)
                if version:
                    observed["rankmath_version"] = version
                else:
                    observed["rankmath_absent"] = True

            # 5. Rank Math AI Visibility. Absent, present-but-unsubscribed and
            #    present-and-working are three states, and only the last is a ✓.
            overview = await probe("rankmath_aiv", f"{AIV_BASE}/overview")
            caps["rankmath_aiv"] = overview is not None
            if overview is not None:
                errors.pop("rankmath_aiv", None)
                data = _unwrap(overview)
                brands = data.get("brands")
                observed["brand_count"] = len(brands) if isinstance(brands, list) else 0

        return caps, errors, observed


def _ability_rows(body: Any) -> list[dict[str, Any]]:
    """One page of the abilities list, whichever envelope it arrived in."""
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    # Core paginates some collections into an envelope; accept either shape rather than
    # reading "no abilities" off a wrapper we did not expect.
    if isinstance(body, dict):
        for key in ("abilities", "items", "data"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _query_input(input: Any) -> dict[str, Any]:
    """An ability's input as ``GET``/``DELETE`` query parameters, PHP-style.

    ``input[key]=value`` is what ``WP_REST_Request::get_query_params()`` folds back into an
    array. A scalar rides as-is; a list becomes repeated keys; a nested object is flattened one
    level (``input[a][b]=v``). Deeper nesting is JSON-encoded, which is the honest limit of a
    query string and one a read-only ability rarely reaches.
    """
    if input is None:
        return {}
    if not isinstance(input, dict):
        return {"input": input}
    out: dict[str, Any] = {}
    for key, value in input.items():
        if isinstance(value, dict):
            for sub, inner in value.items():
                out[f"input[{key}][{sub}]"] = (
                    inner if isinstance(inner, str | int | float | bool) else _json.dumps(inner)
                )
        elif isinstance(value, list):
            out[f"input[{key}][]"] = [
                v if isinstance(v, str | int | float | bool) else _json.dumps(v) for v in value
            ]
        elif isinstance(value, bool):
            out[f"input[{key}]"] = "true" if value else "false"
        else:
            out[f"input[{key}]"] = value
    return out


def mcp_server_route(namespaces: list[str], routes: Any) -> str | None:
    """The MCP Adapter's server route (``/mcp/<server>``) from a site's REST index, or ``None``.

    The namespace is the bare ``mcp`` (an older adapter may have registered it with a slash, so
    both spellings count); the servers are the routes exactly one segment below it. Preferring
    the one that lists ``POST`` picks the transport over a discovery-only route, and the
    default server over an OAuth metadata one, without naming either.
    """
    if not any(n == MCP_NAMESPACE or n.startswith(MCP_ROUTE_PREFIX[1:]) for n in namespaces):
        return None
    if not isinstance(routes, dict):
        return None
    candidates: list[tuple[int, str]] = []
    for route, spec in routes.items():
        if not isinstance(route, str) or not route.startswith(MCP_ROUTE_PREFIX):
            continue
        tail = route[len(MCP_ROUTE_PREFIX) :]
        if not tail or "/" in tail or "(" in tail:
            continue
        methods: set[str] = set()
        if isinstance(spec, dict):
            for endpoint in spec.get("endpoints") or []:
                if isinstance(endpoint, dict):
                    methods.update(m for m in endpoint.get("methods") or [] if isinstance(m, str))
        candidates.append((0 if "POST" in methods else 1, route))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _rankmath_version(plugins: list[Any]) -> str | None:
    """Rank Math's version from core's ``wp/v2/plugins`` list, or ``None`` if it is not there.

    Matched on the **plugin file prefix** (``seo-by-rank-math/``) rather than the display name:
    the name is translated on a Dutch install and a fork could carry it, while the directory is
    what wordpress.org fixes.
    """
    for row in plugins:
        if not isinstance(row, dict):
            continue
        plugin = row.get("plugin")
        if isinstance(plugin, str) and plugin.startswith(RANKMATH_PLUGIN_PREFIX):
            version = row.get("version")
            return str(version) if version else None
    return None


def _unwrap(body: Any) -> dict[str, Any]:
    """Rank Math's ``{success, data}`` envelope, or the payload itself.

    Both shapes are real: the controllers wrap in ``Base_Controller::success()``, and a future
    route (or a caching layer in front of one) may not. Reading only the wrapper would turn a
    perfectly good payload into "this client has no brands", which is indistinguishable from
    the truth on a screen.
    """
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict) and "success" in body:
        return data
    return body
