"""``snelstart`` models (epic #377, issue #31). Business-licensed — see LICENSE.

Four tables, and the split between them is the whole design.

**`snelstart_accounts`** is *a credential and the administration it opens* — a **row, not a
settings singleton**, for the reason ``mollie_accounts`` and ``oxxa_accounts`` are rows: a
koppelsleutel names exactly one SnelStart administration, and an agency that keeps its own
books beside a client's holds two. A singleton would have made the second one an overwrite,
which for a ledger means invoices booked into the wrong company's accounts.

**`snelstart_links`** is what SnelStart knows about a schakl record, one row per pairing. It
follows the ``cloudflare`` rule to the letter: **what we decided and what we last observed live
in different columns.** ``push_hash`` is what we sent; ``observed`` is what SnelStart answered
when we last looked. A single ``synced`` boolean would have folded those together and made
"somebody edited this relation in SnelStart" unsayable — which is exactly the state an agency
needs to see, because the bookkeeper editing it is usually right.

**`snelstart_refs`** caches the administration's own vocabulary — grootboeken, dagboeken,
kostenplaatsen, landen, artikelomzetgroepen. Not data we own and never authoritative: it exists
so a settings screen can offer a picker without a round-trip, and so a nightly push can resolve
"grootboek 8200" to a uuid without asking first. One table rather than five because nothing
here is ever queried except *by kind, for this account*.

**`snelstart_sync_runs`** is #31's *"failures are visible"* requirement made structural. A sync
that half-worked writes what it did and what it could not do, and the settings screen renders
it. A finance integration whose last error lives only in a log line is a finance integration
nobody trusts.

What is deliberately **not** here: any copy of an invoice, a payment or a ledger total.
SnelStart is the system of record for finance (#31) and a second copy in this module is how two
screens start disagreeing about what a client owes. A settled invoice becomes an ordinary
``InvoicePayment`` in ``invoicing``; the only thing kept here is *the identity* of the SnelStart
document and when we last looked at it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class SnelstartAccountStatus(StrEnum):
    """Whether the stored credential still works. ``error`` is set by whatever found out.

    ``pending`` is a third real state and not a nicety: with the activation flow the row exists
    before the koppelsleutel does, because we created it to mint the ``referenceKey`` that
    SnelStart will quote back to us. Rendering that as ``error`` would tell an admin something
    is broken during the ten seconds in which everything is going exactly to plan.
    """

    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"


class SnelstartConnectMethod(StrEnum):
    """How the koppelsleutel arrived. Recorded because it changes what "disconnect" means.

    A ``manual`` key was typed by a human and deleting the row is the end of it. A ``coupling``
    key was granted through SnelStart's activation flow, and SnelStart may later POST
    ``ActionType: "Delete"`` for it — so the row must be findable from a webhook that knows
    only our own ``referenceKey``.
    """

    MANUAL = "manual"
    COUPLING = "coupling"


class SnelstartLinkKind(StrEnum):
    """Which of schakl's records this pairing is about."""

    RELATION = "relation"
    ARTICLE = "article"
    INVOICE = "invoice"


class SnelstartLinkStatus(StrEnum):
    """Deliberately six values, not a boolean.

    ``pending`` — paired but never pushed. ``active`` — pushed, and SnelStart still agrees.
    ``drift`` — it is there and somebody changed it *in SnelStart*, which is a thing an agency's
    bookkeeper legitimately does and which we must never silently overwrite. ``missing`` — the
    document we created is gone. ``error`` — SnelStart refused. ``unlinked`` — it exists in
    SnelStart and nothing in schakl matches it, which is the state that makes a first connect
    reviewable instead of a leap of faith.

    Each of those needs a different button, which is the test for whether a status column has
    earned its values (the ``cloudflare`` redirect rule).
    """

    PENDING = "pending"
    ACTIVE = "active"
    DRIFT = "drift"
    MISSING = "missing"
    ERROR = "error"
    UNLINKED = "unlinked"


class SnelstartRefKind(StrEnum):
    """A slice of the administration's own vocabulary."""

    LEDGER = "ledger"
    JOURNAL = "journal"
    COST_CENTRE = "cost_centre"
    COUNTRY = "country"
    REVENUE_GROUP = "revenue_group"
    VAT_RATE = "vat_rate"


class SnelstartSyncKind(StrEnum):
    """What a run was trying to do. One vocabulary for the screen and the cron alike."""

    REFERENCE = "reference"
    RELATIONS = "relations"
    ARTICLES = "articles"
    INVOICES = "invoices"
    PAYMENTS = "payments"


class SnelstartAccount(
    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """One SnelStart administration the tenant has connected.

    Auditable (§16): connecting, rotating or removing the credential that writes an agency's
    ledger is exactly the change somebody needs attributed later. The key itself is never part
    of the trail — only the fact that it changed, and by whom.
    """

    __tablename__ = "snelstart_accounts"
    __entity_type__ = "snelstart_account"
    __activity_read_permission__ = "snelstart.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_snelstart_accounts_org_name"),
        # The referenceKey's secret half is compared in constant time, but it is also the only
        # thing that routes an unauthenticated coupling webhook to a tenant, so it must be
        # unique across the whole instance rather than merely within an org.
        UniqueConstraint("connect_secret", name="uq_snelstart_accounts_connect_secret"),
        Index("ix_snelstart_accounts_org_active", "org_id", "active"),
    )

    #: Tenant free text ("SnelStart — Breik", "Boekhouding 2026"). Not i18n'd: it names a thing
    #: the tenant owns, like ``providers.name``.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The koppelsleutel, Fernet at rest (:mod:`app.core.crypto`), write-only through the API.
    #: **Never** in a response, a log line or an error. Nullable for exactly one reason: the
    #: activation flow creates the row *before* SnelStart posts the key back to us, and a row
    #: with no key yet is ``status = pending``, not a broken row.
    client_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The partner subscription key, when this tenant supplies their own. ``NULL`` means *use
    #: the instance's* (``SCHAKL_SNELSTART_SUBSCRIPTION_KEY``) — which is the normal case on
    #: cloud, where one certified partner key serves every tenant, and the exception on a
    #: self-hosted box whose agency registered their own developer account. Per-account rather
    #: than per-org because a tenant testing a second administration against a test product is
    #: the whole reason accounts are rows.
    subscription_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    connect_method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SnelstartConnectMethod.MANUAL.value,
        server_default=SnelstartConnectMethod.MANUAL.value,
    )

    #: The secret half of the ``referenceKey`` this account is named by in SnelStart's coupling
    #: webhook — the ``{org}.{account}.{secret}`` pattern ``app.core.payments.tokens`` already
    #: uses for Mollie. Regenerated whenever the credential is replaced: a coupling revoked
    #: because it leaked must not leave the previous reference answering.
    connect_secret: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- observed at SnelStart --------------------------------------------------- #
    #: ``companyInfo.administratieIdentifier``. The answer to *"which books did I just
    #: connect?"*, and the reason ``verify`` reads ``/companyInfo`` rather than pinging: a
    #: credential that merely works still lets somebody connect the wrong company's ledger.
    administration_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    administration_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: The administration's own settings as SnelStart reports them — its seller block, its
    #: financial year, and the two article-code rules (:attr:`article_code_kind`). Stored whole
    #: because it is an **observation**, and re-reading it is the only way to notice that the
    #: books moved to a new financial year or that the bookkeeper turned on the small-business
    #: scheme. Never a setting: changing any of it happens in SnelStart.
    company_info: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    #: ``Numeriek`` or ``Alfanumeriek``, and the maximum length — both **per administration**,
    #: read from ``companyInfo``. Lifted out of :attr:`company_info` into their own columns
    #: because they are the validation an article push is refused by, and a rule that decides
    #: whether a write succeeds should not have to be dug out of a blob.
    article_code_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    article_code_max_length: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: What the last minted bearer token said it may do (``relaties:write``, …). An observation,
    #: recorded so the screen can say *"this key cannot write invoices"* before a sync fails
    #: halfway rather than after.
    scopes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # --- decided here ------------------------------------------------------------ #
    #: The grootboek an invoice line books to when its tax rate names none. Stored as
    #: SnelStart's **grootboeknummer** (``"8200"``), not its uuid: the number is what a
    #: bookkeeper says out loud, it survives a restore into a fresh administration, and the
    #: uuid is resolved from :class:`SnelstartRef` at push time. Same reasoning as
    #: ``TaxRate.ledger_code``, which is where a *per-rate* choice lives.
    default_ledger_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    #: Push issued invoices automatically, or only when somebody presses the button. Off by
    #: default: #31 says do not auto-finalise financial documents, and an agency connecting an
    #: existing administration wants to watch the first few land before trusting a cron with
    #: its ledger.
    auto_push_invoices: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Attach the rendered PDF to the boeking. On by default — SnelStart's own
    #: ``factuurAlsBijlageVerkoopboeking`` setting expects one, and a boeking without the
    #: document it books is the thing an accountant asks for at year end.
    attach_invoice_pdf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: Book a paid invoice back into schakl as an ``InvoicePayment`` when SnelStart says its
    #: outstanding balance reached zero. On by default: it is the answer to "who hasn't paid",
    #: which is the reason an agency wants this integration at all (#31 scope item 4).
    pull_payments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    #: The user-facing "which provider is this" label (#89). SET NULL: deleting a catalog row
    #: must never take a working credential with it.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SnelstartAccountStatus.PENDING.value,
        server_default=SnelstartAccountStatus.PENDING.value,
    )

    #: Three timestamps, because they are three separate authorities. "We proved the
    #: credential" is not "we read the vocabulary" is not "we pushed what we owe" — and an
    #: integration that folds them into one ``last_sync`` cannot tell an admin which half is
    #: stale (the ``cloudflare_accounts`` finding).
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reference_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: SnelStart's own untranslatable text for the last failure. Read by a human on the
    #: settings screen; never in an error envelope, whose ``message`` is an i18n key (§9).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SnelstartLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One pairing between a schakl record and a SnelStart one.

    ``local_id`` carries **no foreign key** on purpose: it points across a module boundary (§6)
    at a company, a product or an invoice, and the link is also the only record that a SnelStart
    row exists at all — an ``unlinked`` relation has no local row to reference. ``company_id``
    *is* a real column and a real FK, because it is what the company horizon (#285) matches on,
    and a link with no anchor would have filtered nothing at all for a restricted staff member.
    """

    __tablename__ = "snelstart_links"

    __table_args__ = (
        # One schakl record pairs with one SnelStart record per account. Partial, because
        # ``local_id`` is NULL for everything SnelStart holds that schakl does not.
        Index(
            "uq_snelstart_links_local",
            "org_id",
            "account_id",
            "kind",
            "local_id",
            unique=True,
            postgresql_where=text("local_id IS NOT NULL"),
        ),
        UniqueConstraint(
            "org_id", "account_id", "kind", "external_id",
            name="uq_snelstart_links_external",
        ),
        Index("ix_snelstart_links_account_kind", "account_id", "kind", "status"),
        Index("ix_snelstart_links_company", "org_id", "company_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The horizon anchor (#285). Set for a relation link (the company it is about) and for an
    #: invoice link (the invoice's client); NULL for an article, which belongs to no client.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )

    #: ``company`` | ``product`` | ``invoice`` — the same vocabulary
    #: ``invoicing_external_refs.local_type`` uses, so the two can be read together.
    local_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    local_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    #: SnelStart's uuid for the row. A string rather than a UUID column because SnelStart's
    #: ``verkoopfactuur`` and its ``verkoopboeking`` are two different ids for one document and
    #: a future resource may not be uuid-keyed at all.
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    #: What a human calls it there — ``relatiecode``, ``artikelcode``, ``factuurnummer``. Shown
    #: on the review screen, because a uuid tells an admin nothing about which client this is.
    external_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SnelstartLinkStatus.PENDING.value,
        server_default=SnelstartLinkStatus.PENDING.value,
    )

    #: A digest of exactly what we last sent. Comparing the *next* payload against it is what
    #: keeps a nightly sync from rewriting five hundred unchanged relations — and comparing
    #: :attr:`observed` against it is what turns "somebody edited this in SnelStart" from an
    #: invisible overwrite into a reported drift.
    push_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: What SnelStart last said this row looks like. The counterpart to :attr:`push_hash`, and
    #: the reason the two are separate columns rather than one ``synced`` flag.
    observed: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: SnelStart's ``modifiedOn`` for the observed row, so an incremental read can ask for
    #: everything changed since without re-reading the whole administration.
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SnelstartRef(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One entry of the administration's own vocabulary, cached.

    Never authoritative and never edited here — it is a copy of somebody else's list, kept so a
    picker renders without a round-trip and so "grootboek 8200" resolves to a uuid at push time
    without asking. A stale entry is corrected by re-reading, never by writing back.
    """

    __tablename__ = "snelstart_refs"

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "kind", "external_id", name="uq_snelstart_refs_external"
        ),
        Index("ix_snelstart_refs_lookup", "account_id", "kind", "code"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The number or code a bookkeeper says out loud — ``8200``, ``NL``. What a stored mapping
    #: refers to, because a uuid does not survive a restore into a fresh administration.
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: The rest of the row, verbatim: a grootboek's ``grootboekfunctie`` and ``btwSoort``, a
    #: dagboek's ``soort``, a country's ISO codes. Kept whole because which parts matter turns
    #: out to depend on what is being pushed, and re-reading is a network call.
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class SnelstartSyncRun(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """What one sync did, and what it could not do.

    #31's *"failures are visible, retryable, and notified"* made structural. A finance sync
    whose last error lives only in a log line is one nobody can trust, and "it says it worked"
    is not the same sentence as "it wrote 37 of 40 and here are the three".
    """

    __tablename__ = "snelstart_sync_runs"

    __table_args__ = (
        Index("ix_snelstart_sync_runs_recent", "org_id", "account_id", "created_at"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: ``True`` only when everything the run set out to do happened. A run that pushed 37 of 40
    #: is **not** ok — it is a run with three things still to do, and rounding that up to
    #: success is how a client goes uninvoiced for a month.
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: ``{"read": 40, "created": 3, "updated": 1, "skipped": 36, "failed": 0}`` — whatever the
    #: run counts. Free-shaped because a payments run and an articles run count different
    #: things, and a column per verb would be mostly NULL.
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Per-row failures: ``[{"local_id": …, "name": …, "code": "BOE-0021", "message": "…"}]``.
    #: Bounded by the service, because a run against a broken credential would otherwise store
    #: one entry per row in the administration.
    errors: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: SnelStart's own words when the run failed as a whole rather than per row.
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Who asked. NULL for the cron, which is a real and different answer from "an admin
    #: pressed sync" when somebody is working out why the ledger changed at 04:40.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
