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
    #: When the thirteen-month fill last **completed**. NULL means it has not, which is a state
    #: the nightly run acts on rather than a fact nobody reads (#381).
    #:
    #: The backfill was a one-off enqueued when an account is linked, and the enqueue is
    #: explicitly best-effort — "a queue miss is not fatal, the nightly run catches up", which
    #: was not true of anything: the nightly run re-pulls a trailing week and has no opinion
    #: about the year behind it. Thirteen accounts on the live instance therefore held seven to
    #: eleven days each, and every report for a past month printed a Google Ads section of
    #: zeros. A column that says whether the work was ever finished turns "we queued something
    #: once" into a condition the scheduler can retry, which is what the comment already
    #: promised.
    #:
    #: Stamped only on a **complete** run, so a backfill that halts on a bad credential is
    #: re-attempted nightly and stops costing anything the moment it succeeds.
    backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: What the nightly sync last said, when it failed. Separate from ``last_error`` on purpose:
    #: verify and sync ask Google different questions, and a sync failing every night against a
    #: credential that verifies perfectly is exactly the state one shared column would hide.
    last_sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


def _account_horizon_clause(account_column, *, unattached_visible: bool = False):
    """The company horizon for a table whose client link is its **account's**.

    These rows carry no ``company_id``, so the repository's column match would find nothing and
    therefore filter nothing at all — #285's first failure mode, and the one that leaks rather
    than over-restricting. The model states the join instead, and every repository path picks it
    up. An account attached to no client (the agency's own) stays visible either way: it is not
    company data.

    ``unattached_visible`` is for a table whose own ``account_id`` is nullable, where a NULL row
    means *org-wide configuration* rather than *some account we cannot see*. Without it the join
    is false for NULL and the org's house policy disappears for every company-scoped login — and
    §15 is explicit that org-wide configuration stays readable, gated by its own manage
    permission rather than by the horizon.
    """

    def clause(scope):
        accounts = table(
            "google_ads_accounts", column("id"), column("org_id"), column("company_id")
        )
        joined = account_column.in_(
            select(accounts.c.id).where(
                (accounts.c.company_id.is_(None)) | (accounts.c.company_id.in_(scope))
            )
        )
        return (account_column.is_(None)) | joined if unattached_visible else joined

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


class GoogleAdsPolicy(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """The standing rules an agent must reason inside, for one advertiser or for the agency.

    **It is one table, and ``account_id IS NULL`` is the agency's house policy.** The alternative
    — a per-account table beside a block of columns on ``google_ads_settings`` — is the same
    vocabulary written twice, with two validators and two schemas that drift the first time a
    field is added to one of them. One record type means "resolve the effective policy" is one
    function (:mod:`~app.integrations.google_ads.policy`), and the house row is editable through the
    same endpoint as an account's.

    **It hangs off the account, not off the client**, which is worth stating because the issue
    that asked for it said "per-client". Three reasons, all of them the reasons
    :class:`GoogleAdsAccount` is itself a row: a write always names an *account*, so the policy
    guarding it must be findable from one without guessing; ``company_id`` is nullable, so a
    company-anchored policy could never cover the agency's own account; and one client
    legitimately runs two accounts — a brand and a shop — whose protected brand terms and budget
    ceilings are not the same rules. In the ordinary case an account has a client and the policy
    is per-client by consequence.

    **What is enforced and what is advice is a property of the field, not of this table.**
    ``protected_terms``, ``banned_phrases``, ``max_daily_budget``, ``max_budget_increase_pct``
    and ``max_cpc`` are checked before a mutation leaves the process and refuse it. The rest
    shapes what an agent *proposes* and is handed to it inside the tool payload — never appended
    to a prompt, which is #300's rule: a client's own facts reaching a model as instructions are
    obeyed rather than considered.
    """

    __tablename__ = "google_ads_policies"
    __entity_type__ = "google_ads_policy"
    __activity_read_permission__ = "google_ads.policy.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "account_id", name="uq_google_ads_policies_account"),
        # The house row's uniqueness needs its own **partial** index, because the constraint
        # above does not cover it: Postgres treats NULLs as distinct inside a unique constraint,
        # so `(org, NULL)` may be inserted any number of times. The same trap `dim_key` avoids by
        # being `NOT NULL DEFAULT ''`, in the one place that shape is not available — and the
        # payments rule besides (CLAUDE.md §10): an idempotency guarantee that lives in
        # application code loses the race the database would have won.
        Index(
            "uq_google_ads_policies_house",
            "org_id",
            unique=True,
            postgresql_where=text("account_id IS NULL"),
        ),
    )

    #: ``NULL`` is the org's house policy. CASCADE, like everything describing an account: these
    #: rules mean nothing without the advertiser they are about.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_ads_accounts.id", ondelete="CASCADE"),
        nullable=True,
    )

    # -- enforced ------------------------------------------------------------------------------ #

    #: Terms that must keep serving. A proposed negative keyword is refused when it **would
    #: actually block** one of these under its own match type — not when it merely resembles one.
    #: That distinction is the whole value: an EXACT negative on "beugel kosten" does not block
    #: "beugel", and refusing it would train an agency to switch the guard off.
    protected_terms: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: Phrases that may never appear in ad copy this module writes. Checked after the text is
    #: assembled rather than merely requested of a model (#300).
    banned_phrases: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: A hard ceiling on a daily budget, in the **account's** currency. ``NULL`` inherits the
    #: house value, and a house ``NULL`` means no absolute ceiling — an absolute figure invented
    #: here would be meaningless without knowing the account's scale.
    max_daily_budget: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: How far one change may *raise* a daily budget, as a fraction (``1.0`` = may double). The
    #: relative guard is the one that needs no knowledge of the account, which is why it — and
    #: not the absolute one — carries a built-in default. A decrease is never refused: it cannot
    #: spend money.
    max_budget_increase_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: A ceiling on any bid this module writes, in the account's currency.
    max_cpc: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    # -- advice, handed to an agent inside the payload ----------------------------------------- #

    #: Terms the agency excludes for everybody ("vacature", "wikipedia", "betekenis"). Unioned
    #: with the account's own rather than overridden — a house list an account could silently
    #: replace is a list nobody can rely on.
    always_exclude: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: Below this cost (account currency) a search term is not a candidate for exclusion.
    waste_min_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    waste_min_clicks: Mapped[int | None] = mapped_column(nullable=True)
    #: What this account is for, in the tenant's own words. The house row's prose and an
    #: account's reach a model as **two labelled fields**, never concatenated: the agency's
    #: standing voice and one client's facts are different kinds of claim, and fusing them is how
    #: "our clients never bid on competitor names" and "this client sells competitor parts" end
    #: up as one contradictory paragraph.
    steering: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ad_copy_rules: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _account_horizon_clause(cls.account_id, unattached_visible=True)


class GoogleAdsDecisionSubject(StrEnum):
    """What a recorded decision is *about*."""

    SEARCH_TERM = "search_term"
    KEYWORD = "keyword"
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    BUDGET = "budget"
    AD = "ad"


class GoogleAdsDecisionKind(StrEnum):
    """What was decided. ``KEPT`` is the one that only exists because of this table.

    Everything else is observable from the account afterwards; "we looked at this term and chose
    not to exclude it" leaves no trace anywhere in Google, which is exactly why the same term is
    proposed again next month, and the month after.
    """

    EXCLUDED = "excluded"
    KEPT = "kept"
    ADDED = "added"
    REMOVED = "removed"
    PAUSED = "paused"
    ENABLED = "enabled"
    CREATED = "created"
    BUDGET_CHANGED = "budget_changed"
    BID_CHANGED = "bid_changed"


class GoogleAdsDecision(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only: what was decided about one subject, by whom, and why.

    **The point is that an agent never re-proposes what was already settled.** A search term
    ruled out in March is proposed again in April, and again in May, because nothing in Google
    records a decision *not* to act — and an account manager who says "no, that one converts on
    the phone" three times stops reading the list.

    Three properties, each a way this normally goes wrong.

    **It is append-only and the latest row wins.** A decision reversed in June is not an edit of
    March's row: both are true, and "when did we change our mind about this" is a question an
    agency is asked. So the answer is *derived* — the newest non-withdrawn, unexpired row per
    ``(subject_type, subject_key, scope)``.

    **There is deliberately no unique index.** The payments rule (CLAUDE.md §10) says an
    idempotency guarantee belongs in the database, and it says so because a duplicate
    ``InvoicePayment`` is money counted twice. A duplicate history row is a duplicate history
    row: the service refuses to append one identical to the standing decision, and a race that
    slips two through costs one redundant line in a log, where a unique index would cost a 500
    on an ordinary second call.

    **A decision may expire.** ``expires_on`` is nullable and usually NULL, but a permanent
    silence is the wrong default for a judgement about a market: "not worth excluding at today's
    CPC" stops being true, and without a date nobody ever revisits it.

    Not :class:`AuditableMixin`, and that is not an omission. This table *is* the trail for the
    acts it records — it carries its own snapshotted actor (§16), its own impersonator and its
    own withdrawal. An activity row on top of it would audit an audit log.
    """

    __tablename__ = "google_ads_decisions"
    __entity_type__ = "google_ads_decision"

    __table_args__ = (
        Index(
            "ix_google_ads_decisions_subject",
            "org_id",
            "account_id",
            "subject_type",
            "subject_key",
        ),
        Index("ix_google_ads_decisions_recent", "org_id", "account_id", "created_at"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_ads_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: As it was written or observed, for display.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Casefolded, whitespace-collapsed — the key the lookup matches on. Stored rather than
    #: computed at query time so the index is usable: a keyword is case-insensitive to Google,
    #: and "Gratis Offerte" and "gratis  offerte" are one subject that a raw match reads as three.
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Where the decision applies: ``account``, ``campaign:123``, ``ad_group:456``,
    #: ``shared_set:789``. Part of the identity, because keeping a term in one campaign and
    #: excluding it in another is an ordinary arrangement, not a contradiction.
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="account")
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: Whether Google was changed. A ``KEPT`` decision never is; a ``validate_only`` run records
    #: nothing at all, so this is false only for decisions that were never meant to act.
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: ``manual`` (somebody recorded a judgement) or ``write`` (a mutation recorded what it did).
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    #: What was actually sent and what came back — the resource name, the operation, the amounts.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Snapshotted at record time (§16). An audit trail whose actor evaporates is not one.
    decided_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: Set when the decision was taken through an impersonated session (#296): the request runs
    #: as the target, so an actor alone would name the client for something staff did.
    impersonator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: After this date the decision stops standing and the subject may be proposed again.
    expires_on: Mapped[Date | None] = mapped_column(Date, nullable=True)
    #: Withdrawal rather than deletion: "we decided this and then unsaid it" is itself a fact,
    #: and a row that vanishes takes the reason with it.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _account_horizon_clause(cls.account_id)
