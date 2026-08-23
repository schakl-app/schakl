"""``timeon`` request/response models. Business-licensed — see LICENSE.

Every name is **prefixed**. A bare ``AccountRead`` or ``SyncResult`` would collide with another
module's component of the same name, and FastAPI resolves a collision by qualifying *both* —
silently renaming the other module's schema in the generated client and breaking its callers on
the next ``gen:client``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.timeon.models import (
    ConflictPolicy,
    SyncDirection,
    SyncFrequency,
    TimeonAccountStatus,
    TimeonConflictStatus,
    TimeonLinkKind,
    TimeonLinkOrigin,
    TimeonLinkStatus,
    TimeonSyncKind,
)


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
class TimeonAccountRead(BaseModel):
    """One connected Timeon organisation, as the settings screen sees it. **Never a key.**"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    #: Whether an API key is stored at all. ``False`` is the state between creating the row and
    #: pasting a key — ``pending``, not an error, which is what a screen must not render as one.
    connected: bool = False
    base_url: str | None = None

    organisation_id: int | None = None
    #: What Timeon calls the organisation this key opens. The answer to *"did I connect the right
    #: account?"*, which a credential that merely works cannot give.
    organisation_name: str | None = None
    #: Which optional Timeon fields this organisation has switched on. A push may not carry a
    #: project to an organisation with ``fieldProject`` off, and the screen says so rather than
    #: letting the first run discover it.
    organisation_features: dict[str, bool] = Field(default_factory=dict)

    hours_direction: SyncDirection
    projects_direction: SyncDirection
    conflict_policy: ConflictPolicy
    window_days: int
    history_floor: date | None = None
    protect_invoiced: bool = True
    protect_approved: bool = False
    push_approvals: bool = False
    create_missing_projects: bool = False
    create_missing_users: bool = False
    auto_sync: bool = False

    #: The schedule, and what it resolves to (#388). ``auto_time`` is a **local wall clock** in
    #: ``timezone``; ``next_auto_run_at`` is the instant it comes out as, computed server-side by
    #: the same function the worker decides with — a browser re-deriving it would be a second
    #: opinion about a question the API answered holding the org's zone (#312's rule).
    auto_frequency: SyncFrequency = SyncFrequency.DAILY
    auto_interval_hours: int = 4
    auto_time: time
    #: The zone the schedule is read in, so a screen can *name* it rather than print a bare
    #: number a reader has to guess the meaning of.
    timezone: str | None = None
    #: When an automatic run last fired, and when the next one falls. Both exist because a job
    #: that decides not to run leaves no trace, and five nights of that looked exactly like five
    #: nights of nothing having changed in Timeon (#387).
    last_auto_run_at: datetime | None = None
    next_auto_run_at: datetime | None = None

    active: bool
    status: TimeonAccountStatus
    last_verified_at: datetime | None = None
    last_pull_at: datetime | None = None
    last_push_at: datetime | None = None
    #: Timeon's own words for the last failure. Untranslatable, and shown as-is.
    last_error: str | None = None

    #: ``{"hour.linked": 2814, "hour.conflict": 2, …}`` — what this connection currently holds,
    #: so the screen states the shape of the pairing before anybody presses anything.
    counts: dict[str, int] = Field(default_factory=dict)
    #: Open conflicts, lifted out of ``counts`` because it is the one number that is a *queue*.
    open_conflicts: int = 0


class TimeonAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=500)
    base_url: str | None = Field(default=None, max_length=255)


class TimeonAccountUpdate(BaseModel):
    """An omitted key keeps the stored one — a rotation is stating a new key, never clearing a
    field by not mentioning it (the wholesale-PUT rule, from the safe end)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=500)
    base_url: str | None = Field(default=None, max_length=255)
    active: bool | None = None

    hours_direction: SyncDirection | None = None
    projects_direction: SyncDirection | None = None
    conflict_policy: ConflictPolicy | None = None
    window_days: int | None = Field(default=None, ge=1, le=3650)
    #: Explicit ``null`` clears the floor; omitted leaves it. §18's rule, and it matters: "no
    #: floor" and "do not change the floor" are different instructions and a nullable field
    #: cannot say both without the distinction being stated.
    history_floor: date | None = None
    protect_invoiced: bool | None = None
    protect_approved: bool | None = None
    push_approvals: bool | None = None
    create_missing_projects: bool | None = None
    create_missing_users: bool | None = None
    auto_sync: bool | None = None
    #: The schedule. Unlike ``history_floor`` above, an explicit ``null`` here is *ignored* rather
    #: than meaning "clear": the columns are NOT NULL and "no frequency" is not a state — off is
    #: ``auto_sync=false``, which is the one switch that says it.
    auto_frequency: SyncFrequency | None = None
    auto_interval_hours: int | None = Field(default=None, ge=1, le=24)
    auto_time: time | None = None


class TimeonVerifyResult(BaseModel):
    """The answer to *"does this key work, and what does it open?"*

    ``200`` with ``ok=false`` for a refused credential rather than an error status: the probe
    succeeded and its answer was no, and an exception would roll back the very row that records
    what Timeon said (``mollie``'s rule, and ``require_context``'s transaction).
    """

    ok: bool
    organisation_id: int | None = None
    organisation_name: str | None = None
    #: An i18n key naming what went wrong — a refused key, a blocked edge, an unreachable host.
    #: Three different people fix those three, so they are three keys (#381's rule).
    error_key: str | None = None
    #: Timeon's own untranslated words, for the line under the message.
    detail: str | None = None
    user_count: int | None = None
    project_count: int | None = None
    customer_count: int | None = None


# --------------------------------------------------------------------------- #
# Pairings, conflicts, runs
# --------------------------------------------------------------------------- #
class TimeonLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    kind: TimeonLinkKind
    status: TimeonLinkStatus
    origin: TimeonLinkOrigin
    local_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    external_id: str
    external_name: str | None = None
    external_date: date | None = None
    observed: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    pushed_at: datetime | None = None
    pulled_at: datetime | None = None
    last_error: str | None = None
    #: Resolved for the screen so a list of pairings does not read as a list of integers.
    local_label: str | None = None
    company_name: str | None = None


class TimeonConflictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    link_id: uuid.UUID
    kind: TimeonLinkKind
    status: TimeonConflictStatus
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    #: ``{"minutes": {"local": 120, "remote": 90}}`` — only what differs, in schakl's words.
    differences: dict[str, Any] = Field(default_factory=dict)
    local_snapshot: dict[str, Any] = Field(default_factory=dict)
    remote_snapshot: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime
    resolved_at: datetime | None = None
    resolved_by_user_id: uuid.UUID | None = None
    resolved_by_name: str | None = None
    note: str | None = None
    #: Who the hours belong to and what they say — enough to settle the conflict without
    #: opening the entry, which is what makes the queue workable rather than a list of links.
    user_name: str | None = None
    local_id: uuid.UUID | None = None
    external_id: str | None = None


class TimeonConflictResolve(BaseModel):
    """``keep_local`` writes schakl's version into Timeon, ``keep_remote`` the reverse, and
    ``dismiss`` writes neither and never asks again — which is a real answer, not an evasion:
    "these two rows are allowed to differ" is a thing an agency decides.
    """

    resolution: TimeonConflictStatus
    note: str | None = Field(default=None, max_length=500)


class TimeonSyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    kind: TimeonSyncKind
    dry_run: bool
    ok: bool
    window_from: date | None = None
    window_to: date | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    actor_user_id: uuid.UUID | None = None
    actor_name: str | None = None


class TimeonSyncRequest(BaseModel):
    """What one manual run should do.

    ``dry_run`` defaults to **true**. A sync that writes by default is one whose first press is
    irreversible, and the whole argument for building this rather than an importer (§2) rests on
    an agency being able to watch it before trusting it.
    """

    kind: TimeonSyncKind = TimeonSyncKind.HOURS
    dry_run: bool = True
    #: Override the account's rolling window for this run only — a full resync, or a repair of
    #: one month somebody noticed was wrong. Absent means the account's own ``window_days``.
    window_from: date | None = None
    window_to: date | None = None


class TimeonWorkspaceRead(BaseModel):
    """Everything the sync page's shell draws, in one payload.

    One endpoint rather than five: four reads that each resolve the same account are four round
    trips for one screen (docs/GOOGLE_TAG_MANAGER.md §3a), and this one calls Timeon not at all,
    so the page renders during an outage — which is exactly when somebody opens it.
    """

    accounts: list[TimeonAccountRead] = Field(default_factory=list)
    recent_runs: list[TimeonSyncRunRead] = Field(default_factory=list)
    open_conflicts: list[TimeonConflictRead] = Field(default_factory=list)
    #: The server's own clock, so the screen can say "over 6 uur" about the nightly run without
    #: computing a schedule from the browser's timezone — the mistake #316 records in miniature.
    server_time: datetime
