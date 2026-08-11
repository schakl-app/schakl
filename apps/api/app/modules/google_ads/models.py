"""Google Ads accounts and the credential that reaches them. Business-licensed — see LICENSE.

Two tables in this phase, and the split between them is the point.

:class:`GoogleAdsSettings` is **org configuration**: one developer token per agency, because a
developer token is issued to a manager account and identifies the *agency* to Google, not any
one client. It is a credential, so it is Fernet at rest and write-only through the API.

:class:`GoogleAdsAccount` is **the authority for "which Ads customer is this client's"** — the
one fact two modules were about to answer differently. It is a row rather than a column on
``companies`` for the same reason ``cloudflare`` made its credential a row: an agency runs its
own account *and* its clients', the same apex can legally appear in two of them, and one client
can run two accounts (a brand and a shop). So nothing here ever picks an account for you;
``accounts_for_company`` returns all of them and the caller says which.

``company_id`` is deliberately **not** part of the unique key. Two companies legitimately share
one Ads account — a holding company and its trading name — and ``marketing_links`` has never
constrained it either. What must be unique is the customer id: one row per advertiser per org, so
"the manager we reach it through" and "the connection whose grant syncs it" have exactly one
answer. ``UNIQUE (org_id, customer_id)`` is also what makes :meth:`attach` an upsert that another
module's write path can call without ever risking a unique violation it would surface as a 500.

The observed-vs-decided rule (CLAUDE.md §10, ``cloudflare``) applies: ``descriptive_name``,
``currency_code``, ``time_zone``, ``is_manager`` and ``conversion_tracking_status`` are **what
Google last said**, refreshed by verify/sync and never edited here. What schakl decides is
``company_id`` and ``active``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    column,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class GoogleAdsAccountStatus(StrEnum):
    """Two values, and the second one must be clearable.

    A flag that only ever turns *on* is a bug with a long tail (CLAUDE.md §10): a row nothing is
    wrong with keeps its red line through every sync that works. Whatever sets ``ERROR`` names
    what clears it — here, the next verify or sync that succeeds.
    """

    ACTIVE = "active"
    ERROR = "error"


class GoogleAdsSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """The agency's Google Ads developer token, and the switch that arms writing.

    Auditable because rotating the credential that can spend a client's budget is exactly the
    change an agency needs to attribute later. The token is never in the trail — only that it
    changed.
    """

    __tablename__ = "google_ads_settings"
    __entity_type__ = "google_ads_settings"
    __activity_read_permission__ = "google_ads.settings.manage"

    __table_args__ = (UniqueConstraint("org_id", name="uq_google_ads_settings_org"),)

    #: Fernet-encrypted (:mod:`app.core.crypto`); never returned to a client — the API reports
    #: only whether one is configured, mirroring the Google OAuth client secret.
    developer_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The manager account to present when an account row names none of its own. An agency with
    #: one MCC sets this once and never thinks about it again.
    default_login_customer_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: The kill switch for every mutating route in this module (#: campaigns, budgets, keywords,
    #: negatives). Distinct from the permissions, which decide *who* — this decides *whether*,
    #: for the whole instance, in one place an owner can reach in a hurry. Defaults to ``true``:
    #: the permissions are already admin-only and deny-by-default, so a switch defaulting off
    #: would be a second lock on a door nobody can open anyway.
    writes_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class GoogleAdsAccount(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One Google Ads advertiser this org may report on, and how to reach it."""

    __tablename__ = "google_ads_accounts"
    __entity_type__ = "google_ads_account"
    __activity_read_permission__ = "google_ads.account.read"

    __table_args__ = (
        UniqueConstraint("org_id", "customer_id", name="uq_google_ads_accounts_org_customer"),
        Index("ix_google_ads_accounts_org_company", "org_id", "company_id"),
        Index("ix_google_ads_accounts_org_active", "org_id", "active"),
    )

    #: Ten bare digits, normalised on every write (``core.googleads.normalise_customer_id``) —
    #: the same account arrives hyphenated from a human, bare from the picker and as
    #: ``customers/1234567890`` from a GAQL row, and a table holding all three spellings cannot
    #: enforce its own unique constraint.
    customer_id: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The manager (MCC) this account is reached through. **Load-bearing, not decoration**: an
    #: agency's Google user is granted the *manager*, and therefore holds no direct grant on any
    #: client beneath it — without this header every call against a client account is made by
    #: someone with no access to it and 403s.
    login_customer_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: The client this account advertises for. Nullable: an agency's own account belongs to no
    #: client, and an account discovered before anyone said whose it is should still be linkable.
    #: A NULL row is not company data, so the company horizon leaves it visible (#285).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    #: Whose Google grant syncs this account. SET NULL rather than CASCADE, for the reason
    #: ``marketing_links`` gives: a colleague disconnecting their Google account must not delete
    #: the client's Ads history. The link goes dormant and asks to be reconnected.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- what Google last said (observed, never edited here) ---------------------------------- #
    descriptive_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    #: An IANA name, Google's own for this account. Ads reports its days in **the account's**
    #: timezone, not the org's — so a date range means nothing without it (CLAUDE.md §8: a
    #: function that reasons about a wall clock takes the zone as an argument).
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_manager: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    test_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    conversion_tracking_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Google's 0..1 optimisation score. Numeric rather than float: it is displayed as a
    #: percentage and a binary float renders 0.7 as 69,99999%.
    optimization_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    # -- what schakl decided ------------------------------------------------------------------ #
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # -- health -------------------------------------------------------------------------------- #
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GoogleAdsAccountStatus.ACTIVE.value,
        server_default=GoogleAdsAccountStatus.ACTIVE.value,
    )
    #: Google's own sentence, scrubbed of credentials, truncated to the column. Never i18n'd and
    #: never in the error envelope (§9) — it lives here so an admin can read what Google said.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: What the nightly sync last said, when it failed. Separate from ``last_error`` on purpose:
    #: verify and sync ask Google different questions, and a sync failing every night against a
    #: credential that verifies perfectly is exactly the state one shared column would hide.
    last_sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


def _account_horizon_clause(account_column):
    """The company horizon for a table whose client link is its **account's**.

    These rows carry no ``company_id``, so the repository's column match would find nothing and
    therefore filter nothing at all — #285's first failure mode, and the one that leaks rather
    than over-restricting. The model states the join instead, and every repository path picks it
    up. An account attached to no client (the agency's own) stays visible either way: it is not
    company data.
    """

    def clause(scope):
        accounts = table(
            "google_ads_accounts", column("id"), column("org_id"), column("company_id")
        )
        return account_column.in_(
            select(accounts.c.id).where(
                (accounts.c.company_id.is_(None)) | (accounts.c.company_id.in_(scope))
            )
        )

    return clause


class GoogleAdsDimension(StrEnum):
    """What a stored daily row is *about*.

    Deliberately a short list of **bounded** cardinalities: an account has one row a day, an
    agency campaign list has tens, a device has three. A keyword-level daily table would be
    hundreds of thousands of rows a month per client, to answer a question the live read already
    answers better. Depth stays live; only what a *trend* needs is stored.
    """

    ACCOUNT = "account"
    CAMPAIGN = "campaign"
    DEVICE = "device"


class GoogleAdsMetricDaily(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One day of one thing, stored so a trend costs no Google call.

    The reason it exists is that **a comparison must not be a second round trip**: a tile showing
    this month against the same month last year would otherwise make two live Ads calls per
    client per page load, against a shared daily quota, for numbers that stopped changing weeks
    ago.

    ``metrics`` is JSONB rather than columns because the vocabulary is Google's, not ours, and a
    new metric must not be a migration. It holds exactly what ``reporting.metrics_block``
    produces, so a stored row and a live row are the same shape and no screen needs to know
    which it is looking at.

    Re-pulled for a trailing window every night and **upserted**, because Ads conversions keep
    arriving for days after the click: a day read once is a day read too early.
    """

    __tablename__ = "google_ads_metrics_daily"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "account_id",
            "date",
            "dimension",
            "dim_key",
            name="uq_google_ads_metrics_daily_row",
        ),
        Index("ix_google_ads_metrics_daily_lookup", "org_id", "account_id", "dimension", "date"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_ads_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The day on the **account's** calendar, which is the one Google aggregated it on.
    date: Mapped[Date] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The campaign id or device this row is about; ``""`` for the account-wide row and **never
    #: NULL** — Postgres treats NULLs as distinct in a unique constraint, so a nullable key
    #: column lets the same row be stored twice and the upsert silently becomes an insert.
    dim_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Snapshotted at sync time, so a renamed campaign still reads correctly in last quarter's
    #: chart — §16's rule about actors, applied to a label.
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: The account's currency at sync time. Per row, because an account can change it and last
    #: year's spend was in the old one.
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _account_horizon_clause(cls.account_id)


class GoogleAdsChange(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A mirrored ``change_event`` row, kept because Google's own history is 30 days long.

    That window is the whole reason: "who raised this budget, and when" is a question an agency
    is asked months later, and by then Google no longer knows. Mirroring nightly turns 30 days
    into a permanent record — of the changes made *while we were watching*, which is worth being
    precise about: anything that happened before the account was linked was never mirrored and
    never will be.

    **It is still not a complete audit trail, and no amount of mirroring makes it one.** Google's
    own automatic adjustments — Smart Bidding above all — appear in ``change_event`` nowhere at
    all. Said here as well as in the read's warnings, because the tempting mistake is to treat a
    table with four hundred days in it as authoritative.
    """

    __tablename__ = "google_ads_changes"
    __table_args__ = (
        # Google gives no id, so a row is identified by what it *is*: one resource, changed once,
        # at one instant, by one operation. Re-mirroring an overlapping window is then an upsert
        # rather than a duplicate — and the trailing re-pull guarantees overlap every night.
        UniqueConstraint(
            "org_id",
            "account_id",
            "changed_at",
            "changed_resource",
            "operation",
            name="uq_google_ads_changes_event",
        ),
        Index("ix_google_ads_changes_account_at", "org_id", "account_id", "changed_at"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_ads_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Google reports this in the **account's** timezone. It is resolved to an instant on the way
    #: in, using that account's own zone, so two accounts in two countries sort together and a
    #: DST boundary does not reorder an evening's changes.
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    operation: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    changed_resource: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    campaign: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ad_group: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The Google account that made the change, as Google reports it — **not** a schakl user. A
    #: change made in the Ads interface by the client themselves is exactly what this records.
    changed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    client_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``[{field, from, to}]``: the old and new value per changed field, which is the entire
    #: point of mirroring at all. Values are stringified and capped upstream.
    changed_fields: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _account_horizon_clause(cls.account_id)
