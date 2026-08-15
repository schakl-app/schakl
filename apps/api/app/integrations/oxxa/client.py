"""OXXA API client, per **tenant** credential (issue #296). Business-licensed — see LICENSE.

Implements :class:`app.core.registrar.RegistrarProvider` against OXXA's documented HTTP API
(``https://api.oxxa.com/command.php``, official documentation v1.2). Everything here is written
from that document; **none of it has been exercised against a live account** — no sandbox
credential exists (see ``docs/OXXA.md`` §1). The document itself says its examples are
illustrative and that code must be based on the real response, so every field is read
defensively and nothing assumes a tag is present.

Four properties of this API shape the whole file, and each one is a hazard the Cloudflare client
did not have:

1. **The credential travels in the query string.** OXXA authenticates with ``apiuser`` +
   ``apipassword`` GET parameters; there is no header form. So an ``httpx`` exception, whose
   ``str()`` embeds the full URL, must never reach a log line, a stored ``last_error`` or an
   error envelope. Nothing here formats an httpx error; :func:`redact` is applied to every URL
   that leaves this module, and :class:`OxxaError` carries only OXXA's own ``status_description``.
   ``httpx`` logs ``str(request.url)`` itself at ``INFO`` — see ``docs/OXXA.md`` §2, which is
   where that hazard is written down rather than silently worked around.
2. **The response is ISO-8859-1**, declared in the XML prologue. We parse **bytes** so the
   prologue decides; ``response.text`` would let httpx guess and mangle a Dutch registrant name.
3. **Success is ``status_code``, not HTTP status and not ``order_complete``.** OXXA answers 200
   for business failures, and ``domain_ns_upd``'s own documented *success* example carries
   ``<order_complete>FALSE</order_complete>``. Only the ``XMLOK``/``XMLPEN``/``XMLERR`` prefix
   is load-bearing — and its spacing is inconsistent across commands (``XMLOK 16`` vs
   ``XMLOK18``), so it is normalised before anything looks at it.
4. **Nameservers are a shared, named group, not a per-domain list.** See
   :meth:`OxxaClient.set_nameservers` — the single most dangerous thing in this integration.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from app.core.registrar import RegistrarContact, RegistrarDomain, RegistrarError
from app.core.registrar.backend import RegistrarAuthError

logger = logging.getLogger("schakl.oxxa")

API_URL = "https://api.oxxa.com/command.php"

#: OXXA is a dependency of a *screen*, not of a page load. Reads are quick; a register-wide
#: ``domain_list`` on a large reseller account is not, hence the wider read timeout.
_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=20.0, pool=5.0)

#: Retried once, and only where a retry can help. Note what is **not** here: no 4xx, and no
#: retry at all for the write path's ``nsgroup_add`` (see ``_RETRYABLE_COMMANDS``) — OXXA
#: creates a second group rather than failing, and a duplicate is worse than an error.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Commands safe to replay. Deliberately an allowlist of reads: OXXA documents no idempotency
#: key, so anything that mutates or is billed replays as a second order.
_RETRYABLE_COMMANDS = frozenset(
    {
        "domain_list",
        "domain_inf",
        "identity_get",
        "nsgroup_list",
        "nsgroup_get",
        "dnssec_info",
        "user_tld_list",
        "funds_get",
    }
)

#: Refuse a response larger than this before decoding it (CLAUDE.md §17: every cap is checked
#: *before* the work it bounds). A whole reseller register in one ``domain_list`` is large; a
#: 32 MiB one is a provider fault or an attack, not a register.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: An OXXA nameserver group holds 2–6 nameservers (documented under NSGROUPS).
MIN_NAMESERVERS = 2
MAX_NAMESERVERS = 6

#: Prefix on every nameserver group this module creates. It is how we tell ours from the
#: tenant's, and — because the rest of the alias is a digest of the nameservers themselves — how
#: a retry finds the group the previous attempt made instead of creating a second one.
NSGROUP_PREFIX = "schakl-"

_STATUS_RE = re.compile(r"^\s*(XMLOK|XMLPEN|XMLERR)\s*(\d*)\s*$", re.IGNORECASE)

#: Fragments in OXXA's own ``status_description`` that mean "the credential is wrong". OXXA
#: documents **no** distinct status code for authentication (every failure is ``XMLERR``), so
#: this is a best-effort read of its prose, and everything unmatched stays a generic error
#: rather than being guessed into an auth failure.
_AUTH_MARKERS = ("login", "inloggen", "authenticat", "wachtwoord", "password", "toegang")

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production;
#: unset, a test that forgot to stub fails loudly on connect instead of reaching OXXA.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every client uses. Tests only."""
    global _transport
    _transport = transport


def redact(value: str) -> str:
    """Blank out ``apipassword`` anywhere in a URL or message.

    The one function standing between OXXA's auth design and a password in the activity log.
    Applied to anything derived from a request before it is logged, stored or raised.
    """
    return re.sub(r"(apipassword=)[^&\s]*", r"\1***", value, flags=re.IGNORECASE)


class OxxaError(RegistrarError):
    """An OXXA call failed. ``message`` is OXXA's own ``status_description`` — never a URL."""


class OxxaAuthError(RegistrarAuthError, OxxaError):
    """The credential was rejected (best-effort — OXXA has no distinct auth status code)."""


class OxxaConflictError(OxxaError):
    """Outside state disagrees with what we were about to do, and retrying cannot fix it.

    Its own class because the alternative was materially misleading: a bare :class:`OxxaError`
    carries neither an HTTP status nor an OXXA status code, and the service maps exactly that
    shape to "the registrar could not be reached". A nameserver group somebody edited by hand at
    OXXA would then have told an admin to try again in a moment — advice that can never work,
    in front of the one operation in this module that must not be repointed blind.
    """


def _status_parts(raw: str) -> tuple[str, str]:
    """``"XMLOK18"`` and ``"XMLOK 18"`` both → ``("XMLOK", "18")``. Unknown → ``("", raw)``."""
    match = _STATUS_RE.match(raw or "")
    if not match:
        return "", (raw or "").strip()
    return match.group(1).upper(), match.group(2)


def _text(node: ET.Element | None) -> str | None:
    """A tag's text, trimmed. ``None`` for a missing or empty tag — OXXA uses ``<fax />`` for
    "not set", and an empty string would render as a value in the UI."""
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _find_text(parent: ET.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    return _text(parent.find(tag))


def _yes(value: str | None) -> bool | None:
    """OXXA's ``Y``/``N``. ``None`` when the tag was absent — which is *not* ``False``: a
    ``dnssec`` that was never reported must not render as "DNSSEC off"."""
    if value is None:
        return None
    return value.strip().upper().startswith("Y")


def parse_date(value: str | None) -> date | None:
    """OXXA's three date shapes, from one parser.

    ``domain_list`` answers ``2009-10-06``; ``domain_inf`` answers
    ``04-10-2009 (dd-mm-yyyy)`` — with the format hint *inside the value*; the financial
    commands use bare ``DD-MM-YYYY``. Anything else is dropped rather than guessed: a
    misparsed expiry date silently misprices a renewal.
    """
    if not value:
        return None
    cleaned = re.sub(r"\(.*?\)", "", value).strip()
    if not cleaned:
        return None
    cleaned = cleaned.split()[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    logger.warning("oxxa: unparseable date %r", cleaned)
    return None


def norm_host(value: str | None) -> str:
    """A hostname as we compare it: lowercase, no trailing dot, no surrounding space."""
    return (value or "").strip().lower().rstrip(".")


def nsgroup_alias(nameservers: Sequence[str]) -> str:
    """The deterministic alias for the group holding exactly ``nameservers``.

    Deterministic on the **set**, so two agents (or a retry after a timeout) converge on one
    group instead of littering the account with duplicates. Short because OXXA's alias is a
    display field, and prefixed because everything in this integration must be able to answer
    "did schakl create this?".
    """
    canonical = ",".join(sorted({norm_host(ns) for ns in nameservers if norm_host(ns)}))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"{NSGROUP_PREFIX}{digest}"


class OxxaResponse:
    """One parsed ``<order>`` envelope."""

    def __init__(self, order: ET.Element) -> None:
        self._order = order
        raw = _find_text(order, "status_code") or ""
        self.prefix, self.number = _status_parts(raw)
        self.status_code = raw.strip()
        self.description = _find_text(order, "status_description") or ""
        self.details = order.find("details")
        self.price = _find_text(order, "price")

    @property
    def ok(self) -> bool:
        """``XMLOK`` or ``XMLPEN``. Never ``order_complete`` — see the module docstring."""
        return self.prefix in ("XMLOK", "XMLPEN")

    @property
    def pending(self) -> bool:
        return self.prefix == "XMLPEN"

    def detail_text(self) -> str | None:
        """``<details>`` as a bare string, for the commands that return a handle there."""
        return _text(self.details)


class OxxaClient:
    """One tenant credential's worth of OXXA access. Cheap to construct, one per operation."""

    key: ClassVar[str] = "oxxa"

    def __init__(self, api_user: str, api_password: str, *, base_url: str = API_URL) -> None:
        self._user = api_user
        self._password = api_password
        self._base_url = base_url

    # --- transport ------------------------------------------------------------------------ #
    def _params(self, command: str, params: dict[str, Any]) -> dict[str, str]:
        merged: dict[str, str] = {
            "apiuser": self._user,
            "apipassword": self._password,
            "command": command,
        }
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                merged[key] = "Y" if value else "N"
            else:
                merged[key] = str(value)
        return merged

    def _safe_url(self, command: str) -> str:
        """What this call may be called in a log line: the endpoint and the command, no more."""
        return f"{self._base_url}?{urlencode({'command': command})}"

    async def call(self, command: str, **params: Any) -> OxxaResponse:
        """One authenticated command. Raises :class:`OxxaError` on anything but ``XMLOK``/
        ``XMLPEN``."""
        query = self._params(command, params)
        attempts = 2 if command in _RETRYABLE_COMMANDS else 1
        last_exc: Exception | None = None

        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as http:
            for attempt in range(attempts):
                try:
                    response = await http.get(self._base_url, params=query)
                except httpx.HTTPError as exc:
                    # Never ``str(exc)``: httpx embeds the full URL, password and all.
                    last_exc = exc
                    logger.warning(
                        "oxxa: transport failure on %s (%s)",
                        self._safe_url(command),
                        type(exc).__name__,
                    )
                    if attempt + 1 < attempts:
                        continue
                    raise OxxaError(
                        "the registrar could not be reached", code=None, http_status=None
                    ) from exc

                if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                    continue
                if response.status_code >= 400:
                    if response.status_code in (401, 403):
                        raise OxxaAuthError(
                            "the registrar rejected the credential",
                            http_status=response.status_code,
                        )
                    raise OxxaError(
                        f"the registrar answered HTTP {response.status_code}",
                        http_status=response.status_code,
                    )
                return self._parse(command, response)

        raise OxxaError("the registrar could not be reached") from last_exc  # pragma: no cover

    def _parse(self, command: str, response: httpx.Response) -> OxxaResponse:
        body = response.content
        if len(body) > MAX_RESPONSE_BYTES:
            raise OxxaError("the registrar's response was too large to process")
        # stdlib ElementTree does not resolve *external* entities, but it will happily expand
        # internal ones. OXXA never sends a DTD, so refusing one outright costs nothing and
        # closes the entity-expansion hole without adding a dependency (the repo already parses
        # XML this way in invoicing/ubl.py).
        head = body[:2048].upper()
        if b"<!DOCTYPE" in head or b"<!ENTITY" in body[:8192].upper():
            raise OxxaError("the registrar's response was rejected as unsafe")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            logger.warning("oxxa: unparseable XML from %s", self._safe_url(command))
            raise OxxaError("the registrar's response could not be read") from exc

        order = root.find("order") if root.tag == "channel" else root.find(".//order")
        if order is None:
            raise OxxaError("the registrar's response was not in the expected format")

        parsed = OxxaResponse(order)
        if parsed.ok:
            return parsed
        message = redact(parsed.description or parsed.status_code or "unknown registrar error")
        lowered = message.lower()
        if any(marker in lowered for marker in _AUTH_MARKERS):
            raise OxxaAuthError(message, code=parsed.status_code)
        raise OxxaError(message, code=parsed.status_code)

    # --- RegistrarProvider ----------------------------------------------------------------- #
    async def verify(self) -> dict[str, Any]:
        """Prove the credential works, and bring back the reseller balance while we are here.

        ``funds_get`` is the cheapest authenticated read OXXA has and it answers the question an
        agency actually wants answered on a settings screen: a register whose balance has run
        dry stops renewing domains without telling anyone.
        """
        response = await self.call("funds_get")
        details = response.details
        return {
            "funds_total": _find_text(details, "funds_total"),
            "funds_reserved": _find_text(details, "funds_reserved"),
            "funds_available": _find_text(details, "funds_available"),
        }

    async def suffixes(self) -> list[str]:
        """Every TLD this credential may operate on — the authority for the ``sld``/``tld`` split.

        ``user_tld_list`` returns one element *per TLD, named after the TLD* (``<abogado>…``),
        which is why this reads tag names rather than a list of values.
        """
        response = await self.call("user_tld_list")
        if response.details is None:
            return []
        found = {
            child.tag.strip().lower().lstrip(".")
            for child in response.details
            if child.tag and child.tag.strip()
        }
        return sorted(found)

    async def list_domains(self) -> list[RegistrarDomain]:
        """The whole register in one call (``records=-1``).

        ``domain_list`` carries everything a sync needs *except* DNSSEC, which only
        ``domain_inf`` reports — so a register-wide sync is one request, and the per-domain
        refresh is the explicit "go look" action. That asymmetry is the reason sync is a button
        rather than a cron.
        """
        response = await self.call("domain_list", records=-1)
        if response.details is None:
            return []
        return [self._domain_from_list(node) for node in response.details.findall("domain")]

    def _domain_from_list(self, node: ET.Element) -> RegistrarDomain:
        name = norm_host(_find_text(node, "domainname"))
        sld, _, tld = name.partition(".")
        return RegistrarDomain(
            sld=sld,
            tld=tld,
            contacts=self._contacts(node),
            expires_on=parse_date(_find_text(node, "expire_date")),
            transfer_lock=_yes(_find_text(node, "lock")),
            autorenew=_yes(_find_text(node, "autorenew")),
            nameserver_ref=_find_text(node, "nsgroup"),
            status=_find_text(node, "status"),
        )

    @staticmethod
    def _contacts(node: ET.Element) -> dict[str, str]:
        roles = {
            "registrant": "identity-registrant",
            "admin": "identity-admin",
            "tech": "identity-tech",
            "billing": "identity-billing",
        }
        found = {}
        for role, tag in roles.items():
            value = _find_text(node, tag)
            if value:
                found[role] = value
        return found

    async def get_domain(self, sld: str, tld: str) -> RegistrarDomain | None:
        """``domain_inf`` — the detailed read, and the only one that reports DNSSEC."""
        try:
            response = await self.call("domain_inf", sld=sld, tld=tld)
        except OxxaAuthError:
            raise
        except OxxaError as exc:
            # A domain the agency no longer holds is a fact, not a failure. OXXA reports it as
            # an ordinary XMLERR, so this is the one place a business error is swallowed.
            if exc.code and _status_parts(exc.code)[0] == "XMLERR":
                return None
            raise
        details = response.details
        if details is None:
            return None
        return RegistrarDomain(
            sld=sld,
            tld=tld,
            contacts=self._contacts(details),
            expires_on=parse_date(_find_text(details, "expire_date")),
            transfer_lock=_yes(_find_text(details, "lock")),
            autorenew=_yes(_find_text(details, "autorenew")),
            dnssec=_yes(_find_text(details, "dnssec")),
            nameserver_ref=_find_text(details, "nsgroup"),
        )

    async def get_contact(self, ref: str) -> RegistrarContact | None:
        response = await self.call("identity_get", identity=ref)
        details = response.details
        if details is None:
            return None
        return RegistrarContact(
            ref=ref,
            organisation=_find_text(details, "company_name"),
            first_name=_find_text(details, "firstname"),
            last_name=_find_text(details, "lastname"),
            email=_find_text(details, "email"),
            phone=_find_text(details, "tel"),
            street=" ".join(
                part
                for part in (
                    _find_text(details, "street"),
                    _find_text(details, "number"),
                    _find_text(details, "suffix"),
                )
                if part
            )
            or None,
            postal_code=_find_text(details, "postalcode"),
            city=_find_text(details, "city"),
            country=_find_text(details, "country"),
        )

    async def nameservers_of(self, ref: str) -> list[str]:
        """The nameservers in group ``ref``. Order is not meaningful; the caller compares sets."""
        response = await self.call("nsgroup_get", nsgroup=ref)
        details = response.details
        if details is None:
            return []
        found = []
        for index in range(1, MAX_NAMESERVERS + 1):
            host = norm_host(_find_text(details, f"ns{index}_fqdn"))
            if host:
                found.append(host)
        return found

    async def _find_nsgroup(self, alias: str) -> str | None:
        """The handle of the group named ``alias``, if the account has one.

        Searches by **alias**, never by membership: enumerating members means one
        ``nsgroup_get`` per group, and a reseller account holds many. Ours are findable by name
        because :func:`nsgroup_alias` derives the name from the nameservers.
        """
        response = await self.call("nsgroup_list", alias=alias, records=-1)
        if response.details is None:
            return None
        wanted = alias.strip().lower()
        for node in response.details.findall("nsgroup"):
            if (_find_text(node, "name") or "").strip().lower() == wanted:
                handle = _find_text(node, "handle")
                if handle:
                    return handle
        return None

    async def set_nameservers(self, sld: str, tld: str, nameservers: Sequence[str]) -> str:
        """Delegate ``sld.tld`` to exactly ``nameservers``, and return the group handle used.

        **The most dangerous method in this integration.** OXXA has no per-domain nameserver
        list: ``domain_ns_upd`` takes an ``nsgroup`` *handle*, and a group is a shared object
        whose documentation says in as many words that updating it *"wordt doorgevoerd op alle
        domeinen die gebruik maken van het profiel"* — every domain pointing at it moves. So:

        * we **find or create** a group and never, ever ``nsgroup_upd`` one;
        * the group we look for is named after its own contents, so a retry after a timeout
          reuses the group the first attempt made instead of creating a second;
        * if a group with our name exists but holds different nameservers, somebody edited it by
          hand and we **refuse** rather than repoint their domains (docs/CLOUDFLARE.md §5:
          conflicts are reported, never resolved);
        * if the domain already points at the right group we make no write at all, which is what
          makes the whole call idempotent and therefore safely retryable.
        """
        wanted = []
        for host in nameservers:
            normalised = norm_host(host)
            if normalised and normalised not in wanted:
                wanted.append(normalised)
        if not MIN_NAMESERVERS <= len(wanted) <= MAX_NAMESERVERS:
            raise OxxaError(
                f"a nameserver group holds {MIN_NAMESERVERS}–{MAX_NAMESERVERS} nameservers, "
                f"got {len(wanted)}"
            )

        alias = nsgroup_alias(wanted)
        handle = await self._find_nsgroup(alias)
        if handle is not None:
            existing = set(await self.nameservers_of(handle))
            if existing != set(wanted):
                raise OxxaConflictError(
                    f"the nameserver group {alias} exists at the registrar but holds different "
                    "nameservers; it was changed outside schakl"
                )
        else:
            handle = await self._create_nsgroup(alias, wanted)

        current = await self.get_domain(sld, tld)
        if current is not None and current.nameserver_ref == handle:
            return handle  # already delegated here — no write, so a retry costs nothing

        await self.call("domain_ns_upd", sld=sld, tld=tld, nsgroup=handle)
        return handle

    async def _create_nsgroup(self, alias: str, nameservers: Sequence[str]) -> str:
        params: dict[str, Any] = {"alias": alias}
        for index, host in enumerate(nameservers, start=1):
            params[f"ns{index}_fqdn"] = host
        response = await self.call("nsgroup_add", **params)
        handle = response.detail_text()
        if not handle:
            # Documented to come back in <details>. If it does not, re-read rather than assume
            # the group was not created — assuming would create a second one on the next try.
            handle = await self._find_nsgroup(alias)
        if not handle:
            raise OxxaError("the registrar did not return a handle for the new nameserver group")
        return handle
