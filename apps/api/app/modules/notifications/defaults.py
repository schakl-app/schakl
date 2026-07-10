"""Hardcoded default delivery preferences (issue #16).

The bottom layer of the three-layer resolution (default ← org row ← user row). Kept in code,
not the database, so a fresh org needs no seed rows: a user with no preference row at all
still gets sane routing. Managers override per org, users override per user; a delete of an
override falls back here.

Cadence per the issue: a short list of events are **immediate** (you need them now); the rest
land in the **daily digest at 08:00** (Europe/Amsterdam). Leave events are treated as immediate
too — an approval flow buried in tomorrow's digest is broken (flagged deviation from the
issue's "everything else daily"; three lines to revert if unwanted).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from app.modules.notifications.events import (
    CHANNEL_IN_APP,
    COMPANY_ASSIGNED,
    DIGEST_DAILY,
    DIGEST_IMMEDIATE,
    LEAVE_APPROVED,
    LEAVE_REJECTED,
    LEAVE_REQUESTED,
    PROJECT_ASSIGNED,
    TASK_ASSIGNED,
    TASK_OVERDUE,
)

#: Daily digest lands here in Europe/Amsterdam local time.
DEFAULT_DIGEST_TIME = time(8, 0)
#: A task counts as "due soon" this many days before its due date, unless overridden.
DEFAULT_DUE_SOON_DAYS = 3

#: Events that default to immediate delivery; everything else defaults to the daily digest.
_IMMEDIATE_EVENTS: frozenset[str] = frozenset(
    {
        TASK_ASSIGNED,
        PROJECT_ASSIGNED,
        COMPANY_ASSIGNED,
        TASK_OVERDUE,
        LEAVE_REQUESTED,
        LEAVE_APPROVED,
        LEAVE_REJECTED,
    }
)


@dataclass(frozen=True)
class ResolvedPref:
    """The effective delivery rule for one (user, event) on one channel.

    ``source`` records which layer won (``default`` | ``org`` | ``user``) so the settings UI
    can badge an inherited row versus an explicit override. ``due_soon_days`` and the quiet
    hours come from the *general* row (event-independent), folded in for convenience.
    """

    enabled: bool
    delay_minutes: int
    digest: str
    digest_time: time | None
    digest_weekday: int | None
    channel: str = CHANNEL_IN_APP
    source: str = "default"
    # General-scope values (same for every event of a user), carried for the fan-out.
    due_soon_days: int = DEFAULT_DUE_SOON_DAYS
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    general_source: str = "default"


def default_event_pref(event_type: str) -> ResolvedPref:
    """The in-app default for ``event_type`` before any org/user override."""
    if event_type in _IMMEDIATE_EVENTS:
        return ResolvedPref(
            enabled=True,
            delay_minutes=0,
            digest=DIGEST_IMMEDIATE,
            digest_time=None,
            digest_weekday=None,
        )
    return ResolvedPref(
        enabled=True,
        delay_minutes=0,
        digest=DIGEST_DAILY,
        digest_time=DEFAULT_DIGEST_TIME,
        digest_weekday=None,
    )
