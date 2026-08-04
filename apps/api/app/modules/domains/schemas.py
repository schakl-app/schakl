"""Pydantic schemas for the domains module (issue #90, CLAUDE.md §9).

``DomainRead`` returns the raw ``*_provider_id`` FKs **and** resolved display helpers
(``company_name``, ``*_provider_name``) plus the two parties as labelled ``PartyReadRef``s, so a
client can render a domain row without a second round-trip. ``custom`` carries the tenant's
per-entity custom values (§13).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.billing import AutoInvoiceMode
from app.core.party.schemas import PartyReadRef, PartyRef
from app.modules.domains.models import DomainStatus


def normalize_domain_name(value: object) -> object:
    """Reduce whatever the user typed or pasted to the bare root domain.

    People paste full URLs ("https://www.example.nl/pagina") and type "www." out of habit;
    a domain record is the apex, so scheme, credentials, port, path and a leading "www."
    are all stripped, and the host is lowercased. Non-strings pass through for Pydantic to
    reject; a value that strips to nothing fails the field's ``min_length``.
    """
    if not isinstance(value, str):
        return value
    name = value.strip().lower()
    if "://" in name:
        name = name.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        name = name.split(sep, 1)[0]
    if "@" in name:
        name = name.rsplit("@", 1)[1]
    name = name.split(":", 1)[0]
    # Interleaved so "www." reduces to nothing and ".www.example.nl" to "example.nl".
    while True:
        stripped = name.strip(".")
        if stripped == "www":
            stripped = ""
        elif stripped.startswith("www."):
            stripped = stripped[4:]
        if stripped == name:
            return name
        name = stripped


def tld_of(name: str) -> str | None:
    """The priced suffix of a (normalized) domain name: everything after the first label —
    so ``example.co.uk`` prices as ``co.uk`` — stored without a leading dot; ``None`` for a
    dotless name. Stamped at write time, never reparsed on read (#250)."""
    _, _, rest = name.partition(".")
    return rest or None


_TLD_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def normalize_tld(value: object) -> object:
    """What the user types in the price list ("nl", ".NL", " co.uk ") → the stored form."""
    if not isinstance(value, str):
        return value
    return value.strip().lower().strip(".")


class MxRecord(BaseModel):
    priority: int
    exchange: str


class DomainBase(BaseModel):
    name: str = Field(min_length=1, max_length=253)

    _normalize_name = field_validator("name", mode="before")(normalize_domain_name)
    status: DomainStatus = DomainStatus.ACTIVE
    redirect_url: str | None = Field(default=None, max_length=512)
    registrar_provider_id: uuid.UUID | None = None
    dns_provider_id: uuid.UUID | None = None
    registry_contact: PartyRef | None = None
    email_enabled: bool = False
    email_provider_id: uuid.UUID | None = None
    email_contact: PartyRef | None = None
    #: A per-domain price agreed outside the TLD list (#250); NULL = the TLD price applies.
    price_override: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999.99"))
    #: Whether this domain is invoiced at all (#298). ``None`` = follow the register: bill it
    #: when a registrar register we have read holds it, and while no register is connected.
    invoiceable: bool | None = None
    #: How far the renewal cron takes this domain's invoice by itself, overriding the org
    #: default; ``None`` inherits. Only about the paper — nothing here renews a registration.
    auto_invoice_mode: AutoInvoiceMode | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class DomainCreate(DomainBase):
    company_id: uuid.UUID
    #: When the registration began — anchors the renewal cycle (#250). The web form always
    #: sends it; omitted (an older API consumer), it defaults to the org-local today.
    start_date: date | None = None


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=253)

    _normalize_name = field_validator("name", mode="before")(normalize_domain_name)
    company_id: uuid.UUID | None = None
    status: DomainStatus | None = None
    redirect_url: str | None = Field(default=None, max_length=512)
    start_date: date | None = None
    registrar_provider_id: uuid.UUID | None = None
    dns_provider_id: uuid.UUID | None = None
    registry_contact: PartyRef | None = None
    email_enabled: bool | None = None
    email_provider_id: uuid.UUID | None = None
    email_contact: PartyRef | None = None
    price_override: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999.99"))
    #: Whether this domain is invoiced at all (#298); explicit ``null`` clears the decision back
    #: to "follow the register". Absent leaves it alone — the ``exclude_unset`` split.
    invoiceable: bool | None = None
    #: How far the renewal cron takes this domain's invoice by itself, overriding the org
    #: default; ``None`` inherits. Only about the paper — nothing here renews a registration.
    auto_invoice_mode: AutoInvoiceMode | None = None
    custom: dict[str, Any] | None = None


class DomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    company_id: uuid.UUID
    company_name: str = ""
    status: DomainStatus
    redirect_url: str | None = None
    registrar_provider_id: uuid.UUID | None = None
    registrar_provider_name: str | None = None
    dns_provider_id: uuid.UUID | None = None
    dns_provider_name: str | None = None
    registry_contact: PartyReadRef | None = None
    email_enabled: bool = False
    email_provider_id: uuid.UUID | None = None
    email_provider_name: str | None = None
    email_contact: PartyReadRef | None = None
    # --- pricing & renewal (#250) --- #
    start_date: date
    tld: str | None = None
    price_override: Decimal | None = None
    next_invoice_date: date | None = None
    auto_invoice_mode: AutoInvoiceMode | None = None
    #: The stored decision (#298): ``true``/``false`` explicit, ``null`` = follow the register.
    invoiceable: bool | None = None
    #: What that resolves to *now* — what the renewal cron and the outstanding picker act on.
    invoiceable_effective: bool = True
    #: Which rule decided: ``explicit`` (a person), ``register`` (a connected register that has
    #: been read), ``default`` (no register connected, so it bills as it always has).
    invoiceable_source: Literal["explicit", "register", "default"] = "default"
    #: The register keys holding this registration (``oxxa``, ``cloudflare``) — what lets the
    #: screen say *which* one answered instead of "the register".
    registers: list[str] = Field(default_factory=list)
    #: The price a renewal would draft at today: ``price_override``, else the TLD's current
    #: list price, else NULL. Display-only — an invoice snapshots at draft time, never here.
    resolved_price: Decimal | None = None
    resolved_currency: str | None = None
    # Fetched from public DNS on a schedule (#92, #125); NULL until first checked.
    nameservers: list[str] | None = None
    dnssec: bool | None = None
    mx_records: list[MxRecord] | None = None
    dns_checked_at: datetime | None = None
    custom: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# --- TLD price list (#250) ------------------------------------------------------------- #


class TldPriceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tld: str
    amount: Decimal
    currency: str
    valid_from: date


class TldPriceGroup(BaseModel):
    """One TLD as the price list shows it: the price in effect today, anything scheduled,
    and the history behind it. TLDs an org holds domains under but hasn't priced appear
    with no rows at all — the list's job is also to show what still needs a price."""

    tld: str
    domain_count: int = 0
    currency: str
    current: TldPriceRow | None = None
    upcoming: list[TldPriceRow] = Field(default_factory=list)
    history: list[TldPriceRow] = Field(default_factory=list)


class TldPriceUpsert(BaseModel):
    tld: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
    _normalize_tld = field_validator("tld", mode="before")(normalize_tld)
    amount: Decimal = Field(ge=0, le=Decimal("9999999999.99"))
    #: Defaults to the org-local today; a same-day row is corrected in place (the
    #: subscriptions manual-edit semantics), any other date appends history.
    valid_from: date | None = None


class TldPriceIncreaseRequest(BaseModel):
    """#231's request shape applied to the TLD list: preview and apply share it, the base
    is the price in effect on ``valid_from``, and scope is everything or one TLD."""

    mode: Literal["percent", "amount", "set"]
    #: Percent (5 = +5%) or a currency amount, after ``mode``. Negative = a decrease.
    value: Decimal = Field(gt=Decimal(-100))
    valid_from: date
    #: Narrow to exactly one TLD; NULL = every priced TLD.
    tld: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$",
    )
    _normalize_tld = field_validator("tld", mode="before")(normalize_tld)


class TldPriceIncreaseItem(BaseModel):
    tld: str
    currency: str
    current_amount: Decimal
    new_amount: Decimal
    #: How many of the org's domains resolve to this TLD price today (overrides excluded) —
    #: the impact the preview is for.
    domain_count: int = 0


class TldPriceIncreaseResult(BaseModel):
    items: list[TldPriceIncreaseItem]
