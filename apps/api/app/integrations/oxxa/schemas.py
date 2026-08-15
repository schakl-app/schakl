"""Pydantic schemas for the oxxa module (issue #296).

Three conventions, the first two inherited from ``cloudflare``:

* **The API password is write-only.** It goes in on create/update and never comes back out — not
  in a read model, not in the OpenAPI spec, not masked. ``password_configured`` is the only
  thing a client learns about it.
* **A status report names its problems as keys, not sentences.** ``issues`` carries stable
  machine strings the web resolves to ``oxxa.issue.*``, so the API never picks a locale for
  somebody else's screen (CLAUDE.md §8).
* **Nameservers go in as a list and are compared as a set.** The registry reorders them freely;
  a client that renders our order as "the" order is fine, one that diffs on it is not.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.oxxa.client import MAX_NAMESERVERS, MIN_NAMESERVERS, norm_host

# --- accounts ----------------------------------------------------------------------------- #


class OxxaAccountRead(BaseModel):
    """A configured OXXA reseller login. Never carries the password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    api_user: str
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    active: bool
    status: str
    #: How many TLDs the credential may operate on. The list itself is an implementation
    #: detail of the ``sld``/``tld`` split; the count is what tells an admin verify has run.
    tld_count: int = 0
    funds_available: Decimal | None = None
    last_verified_at: datetime | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    #: Whether a password is stored at all. The password itself never leaves the server.
    password_configured: bool = True
    #: How many register rows point at this account — the number the settings row prints.
    domain_count: int = 0


class OxxaAccountOption(BaseModel):
    """An account as a *picker* needs it. Separate from :class:`OxxaAccountRead` for the reason
    ``cloudflare``'s is: choosing which register to act through is ``registrar.sync``'s business,
    while seeing how a credential is configured and why it last failed is ``settings.manage``."""

    id: uuid.UUID
    name: str
    active: bool


class OxxaAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    api_user: str = Field(min_length=1, max_length=255)
    #: OXXA sends this in the **query string** on every call. That is its design, not ours, and
    #: it is why nothing in this module ever formats a request URL into a log or an error.
    api_password: str = Field(min_length=1, max_length=512)
    provider_id: uuid.UUID | None = None
    active: bool = True


class OxxaAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    api_user: str | None = Field(default=None, min_length=1, max_length=255)
    #: Omit to keep the stored password; send a new one to rotate. Never an empty string to
    #: clear it — an account without a credential is not a state this module has a use for.
    api_password: str | None = Field(default=None, min_length=1, max_length=512)
    provider_id: uuid.UUID | None = None
    active: bool | None = None


class OxxaAccountVerifyResult(BaseModel):
    """What a verify learned. Never raises for a working-but-limited credential."""

    ok: bool
    funds_available: Decimal | None = None
    #: How many TLDs came back from ``user_tld_list``. Zero after a successful verify means the
    #: credential may not operate on anything — which this module treats as unusable, because
    #: without a suffix list it cannot address a single domain.
    tld_count: int = 0
    #: OXXA's own words when it refused. Untranslatable, so it is reported verbatim here and
    #: stored on the row, never put in the error envelope (§9).
    error: str | None = None


class OxxaAccountSyncResult(BaseModel):
    """The outcome of one register sync."""

    ok: bool
    #: Rows in the register OXXA reported.
    found: int = 0
    #: Register rows newly matched to a schakl domain by name.
    matched: int = 0
    #: In the register, matching no schakl domain — the number worth acting on.
    unmatched: int = 0
    #: Rows whose registry delegation no longer matches what we pushed.
    drifted: int = 0
    error: str | None = None


# --- the register ------------------------------------------------------------------------- #


class RegistrarDomainRead(BaseModel):
    """One row of the register as schakl stores it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    domain_id: uuid.UUID | None = None
    #: The schakl domain's name where one matched — so a list renders without a second query.
    domain_name: str | None = None
    name: str
    sld: str
    tld: str
    expires_on: date | None = None
    transfer_lock: bool | None = None
    autorenew: bool | None = None
    dnssec: bool | None = None
    ns_observed: list[str] | None = None
    ns_desired: list[str] | None = None
    ns_push_status: str
    ns_pushed_at: datetime | None = None
    nsgroup_ref: str | None = None
    contact_refs: dict[str, str] = Field(default_factory=dict)
    registrant: dict | None = None
    registrant_name: str | None = None
    last_error: str | None = None
    last_synced_at: datetime | None = None


class DomainRegistrarStatus(BaseModel):
    """What this module knows about one domain, **from stored rows only**.

    Never calls OXXA. A domain page must not wait on an outside API to render and must still
    render when that API is down (docs/PERFORMANCE.md); ``POST /domains/{id}/refresh`` is the
    explicit "go look" action, mirroring the domains module's own refresh.
    """

    domain_id: uuid.UUID
    #: NULL when the domain is not in any configured register — the ordinary state for a domain
    #: registered somewhere else entirely.
    registrar: RegistrarDomainRead | None = None
    account_id: uuid.UUID | None = None
    account_name: str | None = None
    #: Stable keys the client resolves to ``oxxa.issue.*``.
    issues: list[str] = Field(default_factory=list)
    #: Whether any usable credential exists at all — what tells the panel to offer "configure"
    #: rather than "sync".
    configured: bool = False


class NameserverPush(BaseModel):
    """Ask the registrar to delegate a domain to exactly these nameservers."""

    #: 2–6, the size of an OXXA nameserver group. Normalised and de-duplicated here so the
    #: service compares like with like and the stored ``ns_desired`` is canonical.
    nameservers: list[str] = Field(min_length=MIN_NAMESERVERS, max_length=MAX_NAMESERVERS)
    #: Which register to act through. Required only when the tenant has more than one active
    #: account — this module never picks.
    account_id: uuid.UUID | None = None

    @field_validator("nameservers")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for host in value:
            normalised = norm_host(host)
            # A bare label is not a nameserver, and OXXA would accept it and break delegation.
            if not normalised or "." not in normalised:
                raise ValueError("errors.invalid_hostname")
            if normalised not in seen:
                seen.append(normalised)
        if not MIN_NAMESERVERS <= len(seen) <= MAX_NAMESERVERS:
            raise ValueError("errors.invalid_nameserver_count")
        return seen


class NameserverPushResult(BaseModel):
    """What the push did. ``changed=False`` is a success: the delegation was already right, so
    nothing was written at the registrar — which is what makes a retry free."""

    ok: bool
    changed: bool
    nameservers: list[str] = Field(default_factory=list)
    nsgroup_ref: str | None = None
    error: str | None = None
