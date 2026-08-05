"""A scriptable stand-in for the OXXA registrar API (issue #296).

Modelled on :mod:`tests.cloudflare_fake` — state in plain dicts, a recorded call log, and a way
to make one command fail — but three things about OXXA make it a different animal, and each of
them is a hazard this fake exists to keep honest:

* **The credential travels in the query string.** So this fake records the parsed ``command`` and
  the *non-credential* parameters only, and never the request URL. A fake that logged
  ``str(request.url)`` would put the tenant's API password in every pytest failure output — the
  exact leak ``client.redact`` exists to prevent, reintroduced in the test harness.
  ``test_the_fake_never_records_the_credential`` asserts it.
* **The response is XML with an ISO-8859-1 prologue**, returned as *bytes* — the client parses
  bytes on purpose so the prologue decides the encoding. Handing back a ``str`` here would let
  httpx guess and would quietly stop testing that.
* **Success is the ``status_code`` prefix**, not the HTTP status and not ``order_complete``.
  ``domain_ns_upd``'s own documented success example carries ``<order_complete>FALSE</…>``, so
  that is what this fake sends for it: if anyone ever "fixes" the client to read
  ``order_complete``, every push test goes red.
* **A nameserver group is shared.** ``nsgroup_upd`` is in :data:`FORBIDDEN_COMMANDS` and raises
  rather than answering, because "unknown command" is a *reportable failure* and this one must
  never be reportable — it must be impossible to reach without a red test.

Spacing in the status token is deliberately inconsistent (``XMLOK 16`` vs ``XMLOK18``), exactly
as the official document's examples are, so the client's normalisation stays exercised.
"""

from __future__ import annotations

import itertools
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

import httpx

#: Query parameters this fake must never record. OXXA authenticates with both of them in the
#: URL; ``apipassword`` is the secret and ``apiuser`` is half an identity.
CREDENTIAL_PARAMS = frozenset({"apiuser", "apipassword"})

#: The TLDs a fresh fake credential may operate on. ``co.uk`` earns its place: it is what makes
#: the longest-suffix split in ``split_suffix`` a real test rather than a partition on the dot.
DEFAULT_TLDS = ("nl", "com", "be", "co.uk")

#: Commands this fake refuses to answer at all, loudly.
#:
#: ``nsgroup_upd`` edits a **shared** nameserver group: OXXA's own documentation says the change
#: *"wordt doorgevoerd op alle domeinen die gebruik maken van het profiel"*. The module must
#: therefore find-or-create and never update one. Without this, a regression that started sending
#: it would get the generic "Unknown command" business error back, the client would raise an
#: ordinary ``OxxaError``, and the push would report a tidy failure — a green-ish test run for the
#: one mistake in this integration that repoints a client's live domains. An ``AssertionError``
#: escapes the app instead, so the test that caused it fails by name.
#:
#: Checked **before** ``failures``/``http_failures``/``credentials``, so no test setup can mask it.
FORBIDDEN_COMMANDS = frozenset({"nsgroup_upd"})


def _tag(name: str, value: Any) -> str:
    if value is None:
        return f"<{name} />"
    return f"<{name}>{escape(str(value))}</{name}>"


def _envelope(
    *,
    status_code: str,
    description: str,
    details: str = "",
    price: str | None = None,
    order_complete: str = "TRUE",
    done: str = "TRUE",
) -> bytes:
    """One ``<channel><order>…</order></channel>`` document, as OXXA sends it: bytes, with the
    ISO-8859-1 prologue that decides the encoding."""
    body = (
        f"{_tag('status_code', status_code)}"
        f"{_tag('status_description', description)}"
        f"{_tag('price', price)}"
        f"<details>{details}</details>"
        f"{_tag('order_complete', order_complete)}"
        f"{_tag('done', done)}"
    )
    xml = f'<?xml version="1.0" encoding="ISO-8859-1"?><channel><order>{body}</order></channel>'
    return xml.encode("iso-8859-1", errors="xmlcharrefreplace")


def ok(details: str = "", *, status_code: str = "XMLOK 16", description: str = "Success") -> bytes:
    return _envelope(status_code=status_code, description=description, details=details)


def err(description: str, *, status_code: str = "XMLERR 001") -> bytes:
    return _envelope(
        status_code=status_code, description=description, order_complete="FALSE", done="FALSE"
    )


class FakeOxxa:
    """One OXXA reseller account, holding state, answering ``command.php``.

    A test sets up "the register already holds this domain, pointing at that nameserver group"
    by writing into :attr:`domains` / :attr:`nsgroups` and then asserts on what the module
    *reports*, rather than on what it overwrites.
    """

    def __init__(self) -> None:
        #: ``funds_get``'s answer. A dry reseller balance is a real state worth modelling.
        self.funds: dict[str, str] = {
            "funds_total": "250,00",
            "funds_reserved": "0,00",
            "funds_available": "250,00",
        }
        #: What ``user_tld_list`` reports — the authority for the ``sld``/``tld`` split.
        self.tlds: list[str] = list(DEFAULT_TLDS)
        #: domain name -> the register row.
        self.domains: dict[str, dict[str, Any]] = {}
        #: nsgroup handle -> ``{"alias": …, "nameservers": [...]}``.
        self.nsgroups: dict[str, dict[str, Any]] = {}
        #: identity handle -> the contact's fields, as ``identity_get`` reports them.
        self.identities: dict[str, dict[str, Any]] = {}
        #: command -> ``(status_code, description)``: answer that command as a failure.
        self.failures: dict[str, tuple[str, str]] = {}
        #: command -> an HTTP status to answer with instead of 200 (transport-level trouble).
        self.http_failures: dict[str, int] = {}
        #: When set, a call whose credential does not match is refused the way OXXA refuses a
        #: bad login. It is how a test proves a stored password survived an unrelated PATCH.
        self.credentials: tuple[str, str] | None = None
        #: Every call that arrived, as ``(command, params)`` — **never** the URL, and never the
        #: credential parameters. Asserting on what was *not* called is half the safety story.
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._handles = itertools.count(1)

    # --- fixtures ---------------------------------------------------------------------- #
    def require_credentials(self, api_user: str, api_password: str) -> None:
        self.credentials = (api_user, api_password)

    def fail_command(
        self, command: str, description: str, *, status_code: str = "XMLERR 001"
    ) -> None:
        """Answer ``command`` as an OXXA business failure (HTTP 200 with an ``XMLERR`` token)."""
        self.failures[command] = (status_code, description)

    def add_nsgroup(
        self, alias: str, nameservers: list[str], *, handle: str | None = None
    ) -> str:
        handle = handle or f"NSG-{next(self._handles):06d}"
        self.nsgroups[handle] = {"alias": alias, "nameservers": list(nameservers)}
        return handle

    def add_domain(
        self,
        name: str,
        *,
        nsgroup: str | None = None,
        expires_on: date | None = None,
        lock: bool | None = True,
        autorenew: bool | None = True,
        dnssec: bool | None = False,
        status: str = "active",
        registrant: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "name": name.lower(),
            "nsgroup": nsgroup,
            "expires_on": expires_on or date(date.today().year + 1, 6, 30),
            "lock": lock,
            "autorenew": autorenew,
            "dnssec": dnssec,
            "status": status,
            "registrant": registrant,
        }
        self.domains[row["name"]] = row
        return row

    def add_identity(self, handle: str, **fields: Any) -> str:
        self.identities[handle] = fields
        return handle

    # --- assertions a test reads ------------------------------------------------------- #
    @property
    def commands(self) -> list[str]:
        """Just the command names that arrived, in order."""
        return [command for command, _ in self.calls]

    def params_for(self, command: str) -> list[dict[str, str]]:
        return [params for name, params in self.calls if name == command]

    # --- transport --------------------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        raw = dict(request.url.params)
        command = raw.get("command", "")
        # Record the command and the *non-credential* parameters. Never the URL.
        self.calls.append(
            (command, {k: v for k, v in raw.items() if k not in CREDENTIAL_PARAMS | {"command"}})
        )

        if command in FORBIDDEN_COMMANDS:
            raise AssertionError(
                f"the module sent {command!r} to OXXA. A nameserver group is a shared object: "
                "updating one repoints every domain that points at it. This module must "
                "find-or-create and never update one."
            )

        if command in self.http_failures:
            return httpx.Response(self.http_failures[command])
        if self.credentials is not None and (
            raw.get("apiuser"),
            raw.get("apipassword"),
        ) != self.credentials:
            return httpx.Response(
                200, content=err("Login incorrect, controleer uw wachtwoord")
            )
        if command in self.failures:
            status_code, description = self.failures[command]
            return httpx.Response(200, content=err(description, status_code=status_code))

        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            return httpx.Response(200, content=err(f"Unknown command {command}"))
        return handler(raw)

    # --- commands ---------------------------------------------------------------------- #
    def _cmd_funds_get(self, _: dict[str, str]) -> httpx.Response:
        details = "".join(_tag(key, value) for key, value in self.funds.items())
        return httpx.Response(200, content=ok(details, status_code="XMLOK18"))

    def _cmd_user_tld_list(self, _: dict[str, str]) -> httpx.Response:
        # One element *per TLD, named after the TLD* — the documented shape, and the reason the
        # client reads tag names rather than values.
        details = "".join(f"<{tld} />" for tld in self.tlds)
        return httpx.Response(200, content=ok(details))

    def _cmd_domain_list(self, params: dict[str, str]) -> httpx.Response:
        rows = []
        for row in self.domains.values():
            rows.append(
                "<domain>"
                + _tag("domainname", row["name"])
                # ``domain_list`` answers ISO dates; ``domain_inf`` does not. Both on purpose.
                + _tag("expire_date", row["expires_on"].isoformat() if row["expires_on"] else None)
                + _tag("lock", _yn(row["lock"]))
                + _tag("autorenew", _yn(row["autorenew"]))
                + _tag("nsgroup", row["nsgroup"])
                + _tag("status", row["status"])
                + _tag("identity-registrant", row["registrant"])
                + _tag("identity-admin", row["registrant"])
                + "</domain>"
            )
        return httpx.Response(200, content=ok("".join(rows)))

    def _cmd_domain_inf(self, params: dict[str, str]) -> httpx.Response:
        name = f"{params.get('sld', '')}.{params.get('tld', '')}".lower()
        row = self.domains.get(name)
        if row is None:
            # An ordinary business error, which is how OXXA reports a domain the account does
            # not hold — the one place the client swallows an XMLERR.
            return httpx.Response(200, content=err(f"Domain {name} not found"))
        expires = row["expires_on"]
        details = (
            _tag("domainname", row["name"])
            # The format hint lives *inside* the value. Documented, and genuinely sent.
            + _tag(
                "expire_date",
                f"{expires.strftime('%d-%m-%Y')} (dd-mm-yyyy)" if expires else None,
            )
            + _tag("lock", _yn(row["lock"]))
            + _tag("autorenew", _yn(row["autorenew"]))
            + _tag("dnssec", _yn(row["dnssec"]))
            + _tag("nsgroup", row["nsgroup"])
            + _tag("identity-registrant", row["registrant"])
        )
        return httpx.Response(200, content=ok(details))

    def _cmd_identity_get(self, params: dict[str, str]) -> httpx.Response:
        fields = self.identities.get(params.get("identity", ""))
        if fields is None:
            return httpx.Response(200, content=err("Identity not found"))
        details = "".join(_tag(key, value) for key, value in fields.items())
        return httpx.Response(200, content=ok(details))

    def _cmd_nsgroup_list(self, params: dict[str, str]) -> httpx.Response:
        alias = (params.get("alias") or "").strip().lower()
        rows = [
            "<nsgroup>" + _tag("name", group["alias"]) + _tag("handle", handle) + "</nsgroup>"
            for handle, group in self.nsgroups.items()
            if not alias or group["alias"].strip().lower() == alias
        ]
        return httpx.Response(200, content=ok("".join(rows)))

    def _cmd_nsgroup_get(self, params: dict[str, str]) -> httpx.Response:
        group = self.nsgroups.get(params.get("nsgroup", ""))
        if group is None:
            return httpx.Response(200, content=err("Nameserver group not found"))
        details = "".join(
            _tag(f"ns{index}_fqdn", host)
            for index, host in enumerate(group["nameservers"], start=1)
        )
        return httpx.Response(200, content=ok(_tag("alias", group["alias"]) + details))

    def _cmd_nsgroup_add(self, params: dict[str, str]) -> httpx.Response:
        nameservers = [
            params[f"ns{index}_fqdn"]
            for index in range(1, 7)
            if params.get(f"ns{index}_fqdn")
        ]
        handle = self.add_nsgroup(params.get("alias", ""), nameservers)
        # The handle comes back as the bare text of <details>, per the documentation.
        return httpx.Response(
            200,
            content=_envelope(
                status_code="XMLOK 16", description="Nameserver group added", details=handle
            ),
        )

    def _cmd_domain_ns_upd(self, params: dict[str, str]) -> httpx.Response:
        name = f"{params.get('sld', '')}.{params.get('tld', '')}".lower()
        row = self.domains.get(name)
        if row is None:
            return httpx.Response(200, content=err(f"Domain {name} not found"))
        handle = params.get("nsgroup", "")
        if handle not in self.nsgroups:
            return httpx.Response(200, content=err("Unknown nameserver group"))
        row["nsgroup"] = handle
        # OXXA's own documented *success* example for this command carries
        # ``order_complete=FALSE`` — the registry has not applied it yet. Only the XMLOK prefix
        # is load-bearing, and this is what keeps that true in the tests.
        return httpx.Response(
            200,
            content=_envelope(
                status_code="XMLOK 16",
                description="Nameservers updated",
                order_complete="FALSE",
                done="TRUE",
            ),
        )


def _yn(value: bool | None) -> str | None:
    """OXXA's ``Y``/``N``. ``None`` stays absent — "not reported" is not ``False``."""
    if value is None:
        return None
    return "Y" if value else "N"
