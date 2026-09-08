"""``subscriptions`` — recurring client agreements (issue #30, the retainer half of P2).

A subscription is a **revenue** concept ("this client pays €X per period for this scope"),
distinct from a project's monthly hour budget (a **capacity** concept). They link, they are
not the same row. Decisions recorded on the issue and encoded here:

- **Price history, never a mutating amount** — the price lives in append-only
  ``subscription_prices`` rows with a ``valid_from``; the amount at any past invoice date stays
  answerable, so history never reprices itself.
- **Included hours + rollover are tenant config, not code** — the rollover rule is a JSONB
  blob per subscription (mode + expiry), like leave carry-over (§14).
- **Proration is explicitly unsupported in v1** — a mid-period start invoices its first full
  period on ``next_invoice_date``; the operator adjusts the first invoice by hand.
- **No invoicing engine here** (#31 owns invoices): the cron emits ``subscription.due`` with
  the lines, and the accounting integration consumes it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class SubscriptionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class SubscriptionInterval(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class SubscriptionType(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-configurable kind of subscription (issue #142): hosting, onderhoud, SEO, …

    The contact-types / leave-types shape (``label_i18n`` + ``active`` + ``position``, CRUD
    under Instellingen) — categories are tenant config, never code. Deleting a type SET NULLs
    the subscriptions that carry it (see ``Subscription.subscription_type_id``), so a type can
    always be removed without stranding an agreement; ``active`` hides it from pickers first.
    """

    __tablename__ = "subscription_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_subscription_types_org_key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Per-locale labels ({"nl": ..., "en": ...}) — tenant data, like custom-field labels.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Task templates instantiated on a subscription's *first* activation — plain UUIDs, no FK
    #: into the tasks module's tables (§6, the ``SubscriptionLink`` rule): validated against the
    #: bare table on write, and a template deleted later is simply skipped when spawning.
    task_template_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Which period an invoice raised on the cycle date covers — the year *ahead* of it (hosting,
    #: licences, a registration: paid for before it is delivered) or the month *behind* it (a
    #: retainer, billed once served). A property of what is sold, so it lives on the kind
    #: (``app.core.billing.period_span``); ``False`` is the cycle cron's original reading, which
    #: is what keeps an instance that never touches this billing exactly as it did.
    billed_in_advance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Whether an agreement of this kind keeps a **website** online — hosting, maintenance — and
    #: may therefore be attached to one (``SubscriptionLink`` with ``entity_type = "website"``).
    #: A property of what is sold, like the direction above, so it lives on the kind rather than
    #: on a key the code would have to recognise: the seeded ``hosting`` type ships with it on,
    #: and a tenant's own "Webhosting" ticks it in Instellingen. Read when a website link is
    #: *written*; a link already made survives a later flip, as every stored decision does.
    covers_websites: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class SubscriptionTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A named preset — "Hosting Basis, €25/maand, 1 uur inbegrepen" (issue #142).

    Picking one *prefills* the normal create form (one validation path, no server-side copy),
    so a later change to the preset's money or hours never reaches an agreement already
    signed — those are negotiated per client and move through the price history.

    **The name is the exception.** The create form takes it *from* the preset and shows it
    read-only, so "Hosting Basis" on twelve clients is one label repeated twelve times, not
    twelve independent names. Renaming the preset and leaving them behind would rename
    nothing anyone reads. That is why an agreement now records which preset it came from
    (``Subscription.subscription_template_id``) and a rename follows through — see
    ``SubscriptionTemplateService.update``. The FK is SET NULL, so a preset still deletes
    freely: its agreements keep their name and simply stop following one.
    """

    __tablename__ = "subscription_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subscription_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscription_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    interval: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionInterval.MONTHLY.value
    )
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    included_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    rollover: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The preset's own say on ``SubscriptionType.billed_in_advance``: ``NULL`` follows the
    #: agreement's type, a value overrides it for every agreement made from this preset. Unlike
    #: the money it is **not** copied onto the agreement — it is read live, so the preset stays
    #: the one place a "we bill this in advance" decision is made and corrected.
    billed_in_advance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Default invoice lines: ``[{description, quantity, unit_amount}]`` — a prefill blob, not
    #: rows to query, so JSONB rather than a child table.
    lines: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Whether the notes of every agreement made from this preset print on the invoices it
    #: raises (#259's transparency text, resolved per agreement). Off by default: a note an
    #: agency wrote for itself must never start reaching clients on an upgrade. Read live,
    #: like ``billed_in_advance``, so the preset stays the one place the decision is made —
    #: and one agreement may say otherwise (``Subscription.notes_on_invoice_override``).
    notes_on_invoice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Subscription(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "subscriptions"
    __entity_type__ = "subscription"  # customizable (§13) + auditable (§16)
    __activity_read_permission__ = "subscriptions.subscription.read"  # trail read gate (audit F7)

    __table_args__ = (
        Index("ix_subscriptions_custom", "custom", postgresql_using="gin"),
    )

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """The stricter rule an **external (client) login** reads agreements by.

        The company match is the plain one (``company_id`` is ``NOT NULL``, so there is no
        unattached agreement to exempt), and **a draft is invisible**: it is the agency still
        pricing something, and a client shown it has been told what they are about to be
        charged before anyone decided. `Invoice.__portal_horizon_clause__`'s rule, one module
        over, and on the model for the same reason — the list, its total and the detail then
        answer alike by construction rather than by three predicates agreeing (§15, #285).
        """
        return cls.company_id.in_(scope or frozenset()) & (
            cls.status != SubscriptionStatus.DRAFT.value
        )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Tenant-defined category (#142). SET NULL: a removed type never strands the agreement.
    subscription_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscription_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: The standard subscription this agreement was created from — provenance, not a copy
    #: source: every other preset value was resolved into columns at create time and is the
    #: agreement's own from then on. It exists so a preset *rename* can follow through to the
    #: agreements still carrying its name (see ``SubscriptionTemplate``). SET NULL, so
    #: deleting a preset unlinks rather than strands.
    subscription_template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscription_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionStatus.ACTIVE.value, index=True
    )
    #: Stamped on the *first* transition into ``active`` and never cleared — the once-only
    #: guard for ``subscription.activated`` (#142): pause→resume must not respawn onboarding.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    interval: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionInterval.MONTHLY.value
    )
    #: "every N intervals" — every 2 months is (monthly, 2).
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: When the next ``subscription.due`` fires; the cron advances it by the interval. NULL on
    #: drafts (nothing to invoice yet). Left unset by the operator, the first activation
    #: derives it as the first boundary of the ``start_date`` grid still ahead (#223, and the
    #: rule that a derived cycle date never lands in the past) — the create form doesn't ask
    #: for it. An explicit date is the operator's and is honoured wherever the cron would.
    next_invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    #: Everything up to this date has already been invoiced — by the system this agreement was
    #: migrated from, on paper, by a predecessor. The operator's own statement, never derived:
    #: a period ending on or before it is not outstanding, the backlog does not list it and the
    #: cron rolls past it without drafting. ``NULL`` says nothing.
    billed_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: How far the billing cron takes this agreement's invoice on its own, overriding the
    #: org's default. ``NULL`` means *inherit*, not *off* — the same three-state discipline
    #: the leave schedules use (§14). The vocabulary belongs to ``invoicing``
    #: (``AutoInvoiceMode``); this module stores the choice and puts it on the ``due`` event,
    #: because a cron that resolved the level itself would need to read another module's
    #: settings table (§6).
    #:
    #: It exists per agreement because per-org config cannot express a per-agreement fact: an
    #: agency automating twelve hosting retainers still assembles by hand the one client whose
    #: invoice is argued over every month, and "turn the feature off" is not an answer to that.
    auto_invoice_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: This agreement's own say on which period its invoice covers, over what its standard
    #: subscription and its type say (``SubscriptionType.billed_in_advance``): ``NULL`` follows
    #: them, a value overrides for this one agreement. The same three-state discipline as
    #: ``auto_invoice_mode`` and for the same reason — the kind states the rule and one client
    #: has the other arrangement, negotiated, and "make it a kind of its own" is not an answer a
    #: settings screen can give twelve times. Named ``_override`` because the *resolved* answer
    #: rides the read as ``billed_in_advance`` (``_attach``), and a resolution written onto the
    #: mapped column would be stored on the next flush.
    billed_in_advance_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: This agreement's own say on whether its notes print on the invoices it raises, over
    #: what its standard subscription says (``SubscriptionTemplate.notes_on_invoice``):
    #: ``NULL`` follows the preset (and an agreement following no preset keeps its notes to
    #: itself), a value decides for this one agreement. Same ``_override`` naming and for the
    #: same reason as the direction: the *resolved* answer rides the read as
    #: ``notes_on_invoice`` (``_attach``), so a same-named mapped column would be flushed.
    notes_on_invoice_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Hours of work the fee includes per period; consumption is measured against the time
    #: logged on the *linked* projects (the same aggregate every budget bar reads, #25).
    included_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    #: Tenant-configured rollover rule for unused included hours (§14's "config, not code"):
    #: ``{"mode": "none" | "carry", "expires_after_periods": int | null}``.
    rollover: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SubscriptionPrice(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only price history: the amount valid from a date. The current price is the
    newest ``valid_from <= today``; a change appends, never mutates, so past invoices keep
    the number they were issued at."""

    __tablename__ = "subscription_prices"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_id", "valid_from", name="uq_subscription_prices_from"
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)


class SubscriptionLine(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """What the invoice says, line by line — so an invoice isn't one opaque number."""

    __tablename__ = "subscription_lines"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=1)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SubscriptionLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Attach a subscription to the work it covers (projects, tasks) — the generic
    cross-module shape CLAUDE.md §6 describes; no FK into another module's table."""

    __tablename__ = "subscription_links"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_id", "entity_type", "entity_id",
            name="uq_subscription_links_target",
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # project|task|website
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
