"""``timeon`` models. Business-licensed — see LICENSE.

Four tables, and the reason there are four rather than two is the whole design.

**``timeon_accounts``** is *a credential and a policy*. The credential half is a row rather than
a settings singleton for the reason ``mollie``, ``oxxa`` and ``snelstart`` are rows: an agency
running two Timeon organisations (its own and one it administers) would otherwise have to
choose. The policy half is what makes this a sync at all rather than an importer with a cron —
direction per entity, how far back a scheduled run looks, what it may never touch, and what
happens when both sides changed. ``docs/TIMEON.md`` §2 records why every one of those is a
setting instead of a decision baked into code.

**``timeon_links``** is one pairing, and it carries **three** fingerprints rather than a
``synced`` flag. ``local_hash`` is what schakl's row looked like when the two sides last agreed;
``remote_hash`` is what Timeon's did. Comparing each against *now* answers "which side moved",
and only both moving is a conflict. A single flag would have folded those into one bit and made
the interesting question — *who* changed it — unsayable, which is the ``cloudflare``/``snelstart``
rule (what we decided and what we last observed live in different columns) applied to a
symmetrical sync, where it has to be stated twice because either side may write.

**``timeon_conflicts``** exists because *a decision nobody wrote down gets re-proposed forever*
(#318). A sync that recomputes divergence every night and offers the same twelve rows every
night is a queue nobody reads by the third week. Resolving one — either way, including "leave
them different" — is a stored fact, and the same divergence never comes back.

**``timeon_sync_runs``** is the transparency half. A sync whose last error lives in a log line is
one nobody trusts; a run that pushed 37 of 40 is *not* ok, and rounding that up to success is how
a fortnight of somebody's hours quietly stops arriving.

What is deliberately **not** here: a second copy of a time entry. ``time`` owns what an hour is;
this module owns *the identity of the Timeon row it is paired with* and when we last looked.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
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


class TimeonAccountStatus(StrEnum):
    """Whether the stored key still opens the organisation it was connected to."""

    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"


class SyncDirection(StrEnum):
    """What a sync is allowed to do for one kind of record.

    Four values, and each is a thing an agency actually asks for.

    ``off`` — do not look. The state a cutover ends in, and the state a tenant starts in.
    ``pull`` — Timeon is authoritative; schakl mirrors it. What the migration branch did nightly.
    ``push`` — schakl is authoritative; Timeon mirrors it. The *reverse* cutover, where people
    have moved to schakl and Timeon is kept alive only because the invoicing runs there.
    ``two_way`` — both write, and the conflict machinery earns its keep.

    Stated per **kind** rather than once for the connection because the honest answer usually
    differs: an agency mid-migration pulls hours (people still log there) while pushing projects
    (they are set up here now). One global direction forces the wrong answer on one of them.
    """

    OFF = "off"
    PULL = "pull"
    PUSH = "push"
    TWO_WAY = "two_way"


class ConflictPolicy(StrEnum):
    """What happens when *both* sides changed a paired record since they last agreed.

    ``manual`` is the default and is the only one that is safe without knowing the tenant: a
    conflict is written down, neither side is touched, and somebody decides. The other two exist
    because plenty of agencies genuinely have an authoritative side and would rather not have a
    queue — but they are a decision to overwrite somebody's edit, so they are chosen, never
    inferred.
    """

    MANUAL = "manual"
    SCHAKL_WINS = "schakl_wins"
    TIMEON_WINS = "timeon_wins"


class TimeonLinkKind(StrEnum):
    """Which of schakl's records a pairing is about.

    ``user`` and ``customer`` are pairings too, even though nothing is ever written to either
    side for them: they are *resolution*, and storing them is what stops every run re-deriving
    "which schakl user is Timeon user 2004392" from an e-mail address that somebody may since
    have changed. A resolution that is only ever recomputed is a resolution that silently moves.
    """

    HOUR = "hour"
    PROJECT = "project"
    CUSTOMER = "customer"
    USER = "user"


class TimeonLinkStatus(StrEnum):
    """Seven values, and each one needs a different button — the test for whether a status
    column has earned its vocabulary (the ``cloudflare`` redirect rule).

    ``linked`` — paired and in step. ``pending`` — paired and never yet reconciled.
    ``drift`` — exactly one side moved, and the next run will carry it across (or would, if the
    direction allowed; a drift the direction forbids is drift that stays reported).
    ``conflict`` — both moved. Nothing is written and a :class:`TimeonConflict` names it.
    ``missing`` — the remote row is gone from a window we know we read completely.
    ``error`` — the last attempt to write it was refused.
    ``ignored`` — a human said "these two are not the same thing, stop offering it". Not a
    failure state and never re-derived, for the reason :class:`TimeonConflict` exists at all.
    """

    PENDING = "pending"
    LINKED = "linked"
    DRIFT = "drift"
    CONFLICT = "conflict"
    MISSING = "missing"
    ERROR = "error"
    IGNORED = "ignored"


class TimeonLinkOrigin(StrEnum):
    """Which side created the record this pairing is about.

    Load-bearing for deletions and for nothing else. A row schakl created and pushed, gone from
    Timeon, means somebody deleted it there; a row Timeon created and we pulled, gone from
    Timeon, means the same. But a row that was never ours and was never pushed carries no claim
    at all, and treating its absence as an instruction would delete work on the strength of a
    window we may simply have narrowed.
    """

    TIMEON = "timeon"
    SCHAKL = "schakl"
    ADOPTED = "adopted"


class TimeonSyncKind(StrEnum):
    """What a run set out to do. One vocabulary for the cron, the button and the screen."""

    VERIFY = "verify"
    ADOPT = "adopt"
    USERS = "users"
    PROJECTS = "projects"
    HOURS = "hours"
    FULL = "full"


class TimeonConflictStatus(StrEnum):
    OPEN = "open"
    KEPT_LOCAL = "kept_local"
    KEPT_REMOTE = "kept_remote"
    DISMISSED = "dismissed"


class TimeonAccount(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One Timeon organisation the tenant has connected, and what the sync may do with it.

    Auditable (§16): a change to ``hours_direction`` decides whether somebody's timesheet is
    rewritten tonight, and "who turned two-way on" is exactly the question asked afterwards.
    The key itself is never in the trail — only that it changed, and by whom.
    """

    __tablename__ = "timeon_accounts"
    __entity_type__ = "timeon_account"
    __activity_read_permission__ = "timeon.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_timeon_accounts_org_name"),
        Index("ix_timeon_accounts_org_active", "org_id", "active"),
    )

    #: Tenant free text ("Timeon — breik."). Not i18n'd: it names a thing the tenant owns.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The API key, Fernet at rest (:mod:`app.core.crypto`), write-only through the API. Never in
    #: a response, a log line, an error envelope or the activity trail.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Override for a self-hosted or staging Timeon. ``NULL`` means the public API.
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- observed over there ------------------------------------------------- #
    organisation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organisation_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The organisation row verbatim as Timeon last described it — which optional fields their
    #: instance has switched on (``fieldProject``, ``fieldBillable``, ``enableBillableHours``),
    #: because those decide whether a push may carry a project or a billable flag at all.
    #: An observation, never a setting: all of it is changed in Timeon.
    organisation_info: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # --- what the sync may do ------------------------------------------------ #
    hours_direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SyncDirection.OFF.value, server_default="off"
    )
    projects_direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SyncDirection.OFF.value, server_default="off"
    )
    conflict_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ConflictPolicy.MANUAL.value, server_default="manual"
    )

    #: How far back a *scheduled* run re-reads, in days. This is the one number that matters,
    #: because Timeon's hour rows carry no modified timestamp (``client.py`` rule 4): there is no
    #: "what changed since" to ask, so the window **is** the sync. 45 days by default — long
    #: enough to catch a correction made when last month's invoice was prepared, short enough
    #: that a nightly run reads two months rather than three years.
    window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=45, server_default="45"
    )

    #: Nothing on or before this date is ever read, written or deleted by this integration.
    #: The migration (``docs/TIMEON.md`` §1) marked 2814 historical entries invoiced on the
    #: owner's decision that imported history counts as billed; a sync that then re-read 2024
    #: would find every one of them "changed" and hand an agency a two-thousand-row conflict
    #: queue about work that was settled years ago. ``NULL`` means no floor, which is correct
    #: for a tenant connecting Timeon before anything was imported.
    history_floor: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: An invoiced entry is a **record**, not live data — ``docs/TIMEON.md`` §2 is the argument
    #: that was originally made *against* building this at all, and this flag is the answer to
    #: it rather than a dismissal of it. On (the default), a pull may never rewrite or delete an
    #: entry schakl has invoiced; the divergence is reported instead. Off is a real choice for a
    #: tenant that does not invoice from schakl at all.
    protect_invoiced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: The same rule one notch weaker: approved hours are signed off, and the platform already
    #: locks them for anyone without ``time.entry.approve``. Off by default, because an approval
    #: correction arriving from Timeon is ordinary and an agency mid-migration expects it.
    protect_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Mirror schakl's approvals into Timeon. Separate from ``hours_direction`` because approving
    #: is a different act from logging: an agency may want its sign-offs to travel while the
    #: hours themselves only ever come the other way.
    push_approvals: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Create the schakl project a pulled hour needs. Off means such an hour lands on its client
    #: with no project and the run says so — which is the right default, because a project is a
    #: thing an agency names deliberately and a sync inventing 157 of them is a mess to undo.
    create_missing_projects: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Create a login-less schakl account for a Timeon user who has none. Also off by default,
    #: and for a stronger reason: an account is a person, memberships cost licence seats on some
    #: plans, and the alternative failure — the run reporting "3 people's hours were skipped" —
    #: is loud, correctable and harms nothing.
    create_missing_users: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Run nightly without being asked. Off until somebody has watched a dry run and a real one.
    auto_sync: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TimeonAccountStatus.PENDING.value,
        server_default=TimeonAccountStatus.PENDING.value,
    )

    #: Three timestamps because they are three separate authorities — "we proved the credential"
    #: is not "we read Timeon" is not "we wrote to it", and an integration that folds them into
    #: one ``last_sync`` cannot tell an admin which half is stale.
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Timeon's own untranslatable words for the last failure. Rendered for a human on the
    #: settings screen; never in an error envelope, whose ``message`` is an i18n key (§9).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TimeonLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One pairing between a schakl record and a Timeon one.

    ``local_id`` carries **no foreign key** on purpose: it points across a module boundary (§6) at
    a time entry, a project, a company or a user, and the link is also the only record that a
    Timeon row exists at all — an unadopted remote row has no local row to reference.
    ``company_id`` *is* a real column with a real FK, because it is what the company horizon
    (#285) matches on, and a link with no anchor would filter nothing at all for a restricted
    staff member.
    """

    __tablename__ = "timeon_links"

    __table_args__ = (
        # One schakl record pairs with one Timeon record per account. Partial, because
        # ``local_id`` is NULL for everything Timeon holds that schakl does not.
        Index(
            "uq_timeon_links_local",
            "org_id",
            "account_id",
            "kind",
            "local_id",
            unique=True,
            postgresql_where=text("local_id IS NOT NULL"),
        ),
        UniqueConstraint(
            "org_id", "account_id", "kind", "external_id", name="uq_timeon_links_external"
        ),
        Index("ix_timeon_links_account_kind", "account_id", "kind", "status"),
        Index("ix_timeon_links_company", "org_id", "company_id"),
        # The hours sync walks a date window and needs the links in it without a table scan.
        Index("ix_timeon_links_window", "account_id", "kind", "external_date"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The horizon anchor (#285): the client this pairing is about, where there is one.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )

    local_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Timeon's integer id, stored as text: it is an opaque identifier here, and a second
    #: resource one day may not be integer-keyed.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What a human calls it over there — the project name, the person's name. Shown on the
    #: review screen, because an integer tells nobody which record this is.
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The row's own date, for an ``hour`` link. Not derivable from anything else here, and it is
    #: what lets a windowed run load exactly the links it is about to reconcile.
    external_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TimeonLinkStatus.PENDING.value,
        server_default=TimeonLinkStatus.PENDING.value,
    )
    origin: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=TimeonLinkOrigin.TIMEON.value,
        server_default=TimeonLinkOrigin.TIMEON.value,
    )

    #: What schakl's row looked like the last time the two sides agreed, and what Timeon's did.
    #: Two columns rather than one ``synced`` flag: comparing each against *now* is what answers
    #: "which side moved", and only both having moved is a conflict.
    local_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The remote row as last observed — only the fields the sync compares, never the eighty
    #: string renderings Timeon ships beside them. It is what a push sends back, because
    #: ``hour/save`` replaces rather than patches (``client.py`` rule 3), and it is what the
    #: conflict screen renders as "what Timeon says".
    observed: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TimeonConflict(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Both sides changed one record, and a human decides which is right.

    A row here is a **stored decision**, which is the point (#318): recomputing divergence every
    night and re-offering the same rows is how a queue stops being read. Resolving one is
    recorded even when the resolution is "leave them different" — ``dismissed`` — because that is
    still an answer and re-asking it every night is the failure this table prevents.

    Both snapshots are frozen at detection. Rendering a conflict from live data would mean the
    two halves of one screen were read at different moments, and the diff a person is asked to
    settle would not be the diff that was detected.
    """

    __tablename__ = "timeon_conflicts"

    __table_args__ = (
        Index("ix_timeon_conflicts_open", "org_id", "account_id", "status", "detected_at"),
        # One open conflict per link. A second detection updates the row rather than stacking a
        # duplicate — the same rule ``timeon_links`` states, one level up.
        Index(
            "uq_timeon_conflicts_open_link",
            "org_id",
            "link_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    link_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("timeon_links.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The horizon anchor again — a conflict list is a cross-client read (#285 failure mode 3).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )

    #: ``{"minutes": {"local": 120, "remote": 90}, …}`` — only the fields that actually differ,
    #: in schakl's vocabulary on both sides, so the screen never prints ``secondsBillable`` at
    #: somebody (#300's ``totalUsers`` lesson).
    differences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    local_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    remote_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TimeonConflictStatus.OPEN.value,
        server_default=TimeonConflictStatus.OPEN.value,
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TimeonSyncRun(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """What one run did, what it could not do, and what it deliberately did not look at."""

    __tablename__ = "timeon_sync_runs"

    __table_args__ = (Index("ix_timeon_sync_runs_recent", "org_id", "account_id", "created_at"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("timeon_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A run that changed nothing because it was asked to change nothing. A dry run is a
    #: first-class mode rather than a debugging flag: it is what makes turning two-way on a
    #: decision somebody can *see* before making, which is the whole UX argument (#305 — show
    #: the constraint working rather than removing the control).
    dry_run: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: ``True`` only when everything the run set out to do happened. A run that wrote 37 of 40 is
    #: a run with three things still to do.
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: The window this run actually covered. Recorded because it is the answer to the question a
    #: windowed sync invites — "why is last March still wrong?" — and because a run that says
    #: nothing about its own horizon reads as one that looked at everything.
    window_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: ``{"read": 312, "pulled_new": 4, "pushed": 1, "conflicts": 2, "skipped_user": 3, …}``.
    #: Free-shaped because a projects run and an hours run count different things.
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Per-row failures and per-row refusals-by-design, bounded by the service. A run against a
    #: broken credential would otherwise write one entry per row in the organisation.
    errors: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: What the run could not decide and is *reporting* rather than failing on — an unmapped
    #: person, a client Timeon has and schakl does not. Separate from ``errors`` because nothing
    #: went wrong: these are the run telling an admin what it needs from them.
    warnings: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who asked. NULL for the cron, which is a real and different answer from "an admin pressed
    #: sync" when somebody is working out why a timesheet changed overnight.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
