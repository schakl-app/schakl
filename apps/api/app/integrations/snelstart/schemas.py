"""``snelstart`` request/response models (epic #377). Business-licensed — see LICENSE.

Every name is **prefixed**. A bare ``AccountRead`` or ``SyncResult`` here would collide with
another module's component of the same name, and FastAPI resolves a collision by qualifying
*both* — silently renaming the other module's schema in the generated client and breaking its
web callers on the next ``gen:client``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.snelstart.models import (
    SnelstartAccountStatus,
    SnelstartConnectMethod,
    SnelstartLinkStatus,
)


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
class SnelstartAccountRead(BaseModel):
    """One connected administration, as the settings screen sees it. **Never a credential.**"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    #: Whether a koppelsleutel is stored at all. ``False`` with ``status = pending`` is the
    #: normal state between "start activation" and SnelStart's callback arriving.
    connected: bool = False
    connect_method: SnelstartConnectMethod
    #: Whether this tenant supplied their own partner subscription key rather than using the
    #: install's. Shown because it changes who has to renew it.
    own_subscription_key: bool = False

    administration_id: uuid.UUID | None = None
    #: The name of the administration this key opens. The answer to *"did I connect the right
    #: books?"*, which is the question a credential that merely works cannot answer.
    administration_name: str | None = None
    financial_year: int | None = None
    #: ``Numeriek`` or ``Alfanumeriek``, per administration — what a product code must satisfy.
    article_code_kind: str | None = None
    article_code_max_length: int | None = None
    #: What the token itself says it may do. Empty until a verify has run.
    scopes: list[str] = Field(default_factory=list)

    default_ledger_code: str | None = None
    auto_push_invoices: bool = False
    attach_invoice_pdf: bool = True
    pull_payments: bool = True

    provider_id: uuid.UUID | None = None
    active: bool
    status: SnelstartAccountStatus
    last_verified_at: datetime | None = None
    last_reference_sync_at: datetime | None = None
    last_synced_at: datetime | None = None
    #: SnelStart's own words for the last failure. Untranslatable, and shown as-is.
    last_error: str | None = None

    #: Where to send the tenant to approve the coupling. Empty when the install has no
    #: ``appShortName``, and the screen then offers a paste box and no button.
    activation_url: str = ""
    #: The one URL SnelStart posts koppelsleutels to. Shown because an access proxy has to
    #: allow it and nobody can allow a URL they cannot see.
    coupling_webhook_url: str = ""
    #: ``{"relation.active": 12, "invoice.error": 1}`` — what this account currently holds.
    counts: dict[str, int] = Field(default_factory=dict)

    created_at: datetime
    updated_at: datetime


class SnelstartAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    #: The koppelsleutel from SnelStart Web. Omit to create a **pending** account whose key will
    #: arrive through the activation flow — which is the whole reason this is optional.
    client_key: str | None = Field(default=None, max_length=2000)
    #: This tenant's own partner subscription key. Omit to use the install's.
    subscription_key: str | None = Field(default=None, max_length=255)
    provider_id: uuid.UUID | None = None
    active: bool = True

    _blank = field_validator("client_key", "subscription_key", mode="before")(_blank_to_none)

    @field_validator("name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class SnelstartAccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    #: Omit to keep the stored key; send a new one to rotate. Rotating forgets every observation
    #: made through the old one — a key that now opens different books must not keep the old
    #: administration's name on screen.
    client_key: str | None = Field(default=None, max_length=2000)
    #: An **empty string clears** this one, unlike ``client_key``: falling back to the install's
    #: partner key is a real and common state, whereas an account with no koppelsleutel is
    #: disconnected and that is what deleting is for.
    subscription_key: str | None = Field(default=None, max_length=255)
    provider_id: uuid.UUID | None = None
    active: bool | None = None
    default_ledger_code: str | None = Field(default=None, max_length=50)
    auto_push_invoices: bool | None = None
    attach_invoice_pdf: bool | None = None
    pull_payments: bool | None = None

    _blank_client = field_validator("client_key", mode="before")(_blank_to_none)


class SnelstartVerifyResult(BaseModel):
    """The outcome of testing a connection. **Never raises** — see the service.

    ``ok=False`` with the row still saved is a real and common state: a rejected credential is
    still a stored credential, and telling somebody which one was rejected is more useful than
    refusing to remember what they typed.
    """

    ok: bool
    administration_name: str | None = None
    administration_id: uuid.UUID | None = None
    financial_year: int | None = None
    scopes: list[str] = Field(default_factory=list)
    #: Which halves of the integration this token cannot deliver (``invoices``, ``articles``…).
    #: Reported up front, because a scope discovered mid-sync is a 403 forty rows in.
    missing_scopes: list[str] = Field(default_factory=list)
    #: The administration's own seller block, so the screen can show it beside schakl's. An
    #: agency invoicing from one address while its bookkeeper chases from another has a problem
    #: worth seeing before a client points it out.
    seller: dict[str, Any] = Field(default_factory=dict)
    #: SnelStart's own words. Untranslatable and shown as-is, beside the translated key.
    error: str | None = None
    #: Which of the two credentials was refused, as an i18n key — the whole diagnosis.
    error_key: str | None = None


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
class SnelstartLedgerOption(BaseModel):
    """One revenue account an invoice line may book to."""

    id: str
    #: The grootboeknummer (``8200``) — what a mapping stores and a bookkeeper says out loud.
    code: str
    name: str
    #: ``VerkopenOmzetHoog``, ``DienstverleningBinnenEU``… — shown so a picker can group.
    function: str = ""
    #: Which btw-soorten this account accepts. A rate mapped to an account that refuses it
    #: answers ``BOE-0082``, so the screen warns before the push does.
    vat_kinds: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
class SnelstartLinkRead(BaseModel):
    """One pairing between a schakl record and a SnelStart one."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    kind: str
    company_id: uuid.UUID | None = None
    local_type: str | None = None
    local_id: uuid.UUID | None = None
    #: What a human calls it in SnelStart — ``relatiecode``, ``artikelcode``, ``factuurnummer``.
    external_code: str | None = None
    external_name: str | None = None
    external_id: str
    status: SnelstartLinkStatus
    pushed_at: datetime | None = None
    observed_at: datetime | None = None
    last_error: str | None = None
    last_synced_at: datetime | None = None


class SnelstartLinkAdopt(BaseModel):
    """Pair an existing SnelStart row with a schakl record, by hand.

    The escape hatch matching cannot provide: an agency whose bookkeeper called a client
    *"Jansen bv"* in SnelStart and *"Bakkerij Jansen"* in schakl has two records that no rule
    should pair automatically and a human can pair in one click.
    """

    local_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
class SnelstartSyncRunRead(BaseModel):
    """What one sync did, and what it could not do."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    kind: str
    #: ``True`` only when everything the run set out to do happened. A run that pushed 37 of 40
    #: is not ok — it is a run with three things still to do.
    ok: bool
    counts: dict[str, Any] = Field(default_factory=dict)
    #: Per-row failures, capped. ``counts["failed"]`` stays exact when this is truncated.
    errors: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None
    actor_user_id: uuid.UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None


class SnelstartRelationCandidate(BaseModel):
    """One SnelStart relation and what schakl thinks it is, before anybody agrees.

    The first connect is the dangerous moment: an administration with 200 relations and a CRM
    with 180 companies has an overlap nobody can eyeball, and a sync that decided silently would
    either duplicate every client or merge two that merely share a word. So matching *proposes*
    and a human confirms — which is also the only place the confidence is worth showing.
    """

    external_id: str
    external_code: str | None = None
    name: str
    email: str | None = None
    vat_number: str | None = None
    coc_number: str | None = None
    #: The schakl company we believe this is, if any.
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    #: ``coc`` | ``vat`` | ``client_number`` | ``email`` | ``name`` — *why* we believe it. A
    #: match on the Chamber of Commerce number is a fact; a match on the name is a guess, and an
    #: admin reviewing 200 rows needs to know which ones to actually read.
    match_on: str | None = None
    #: Already paired, so the review screen can show what is done rather than only what is left.
    linked: bool = False
    #: The link row ``POST /links/{link_id}/adopt`` acts on, or ``None`` when none exists yet.
    #:
    #: Without it the review screen can *show* a leftover and not *fix* one: adopting is
    #: addressed by link id, a candidate is addressed by SnelStart's relation id, and nothing
    #: else in the surface maps one to the other — there is no ``GET /links``. ``None`` is a real
    #: and expected state rather than a fault: a link row is created by ``sync/relations``, so
    #: this is empty exactly until that pass has run, and the screen says so instead of drawing
    #: a button with nothing to post to (#253).
    link_id: uuid.UUID | None = None


class SnelstartPushResult(BaseModel):
    """The outcome of pushing one record."""

    ok: bool
    external_id: str | None = None
    external_code: str | None = None
    #: ``created`` | ``updated`` | ``unchanged`` | ``adopted`` — ``unchanged`` is a success and
    #: the most common one, since a nightly sync mostly finds nothing to say.
    action: str | None = None
    #: Rates the administration's own table could not confirm, so a guess was used. Reported
    #: rather than swallowed: "we guessed how to tax this" is a sentence a finance integration
    #: has to say out loud.
    guessed_rates: list[str] = Field(default_factory=list)
    error: str | None = None
    error_key: str | None = None


class SnelstartPaymentReconcileRow(BaseModel):
    """One invoice SnelStart and schakl disagree about."""

    invoice_id: uuid.UUID
    number: str
    #: What SnelStart says is still owed. ``0`` means paid in full.
    outstanding: Decimal
    #: What schakl thinks is still owed, before this run.
    local_outstanding: Decimal
    #: Whether a payment row was written. ``False`` with a non-zero difference is a *partial*
    #: payment, which schakl records too — a client who paid half is not a client who paid.
    booked: bool = False
    amount: Decimal | None = None
