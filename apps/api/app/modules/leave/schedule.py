"""Weekly work schedules and the minute arithmetic every leave hour is derived from (#46).

A schedule says *when* an employee works: per weekday one working block and any number of
break windows inside it. It is stored as JSONB (one column, read whole, never queried by
weekday — the same reasoning as CLAUDE.md §13's "JSONB, not EAV") and validated here on
every write, so the shape is guaranteed at the edge.

**Breaks are windows, not durations.** You cannot subtract "30 minutes" from ``15:00–17:00``:
there is no break in it. A break has a position, so a day's hours are
``(end − start) − Σ overlap(window, break_i)`` — one loop whether the day holds zero, one or
three breaks, and correct for a request that starts in the middle of lunch (#48).

All arithmetic runs in **whole minutes** (int) and converts to ``Decimal`` hours once, at the
end. Rounding a float per day and summing is how a 40-hour week becomes 39.99.
"""

from __future__ import annotations

from datetime import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, model_validator

#: Weekday keys, Monday first (ISO order). ``date.weekday()`` indexes straight into this.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

MINUTES_PER_DAY = 24 * 60


def to_minutes(value: time) -> int:
    """Minutes since midnight — the unit every window in this module is expressed in."""
    return value.hour * 60 + value.minute



def _format(value: time) -> str:
    """``08:30`` — the wire format. Seconds would be noise nobody can enter."""
    return f"{value.hour:02d}:{value.minute:02d}"


def _truncate(value: time) -> time:
    return value.replace(second=0, microsecond=0)


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Minutes the two half-open intervals share (0 when they don't touch)."""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


#: Minute-resolution ``time`` that reads and writes ``"HH:MM"``.
Clock = Annotated[time, PlainSerializer(_format, return_type=str)]


class BreakWindow(BaseModel):
    """An unpaid interval inside the working block — lunch, a coffee break, both."""

    start: Clock
    end: Clock

    @model_validator(mode="after")
    def _ordered(self) -> BreakWindow:
        self.start, self.end = _truncate(self.start), _truncate(self.end)
        if to_minutes(self.start) >= to_minutes(self.end):
            raise ValueError("errors.leave_schedule_break_invalid")
        return self


class WorkDay(BaseModel):
    """One working block plus its breaks. ``breaks: []`` is an uninterrupted day."""

    start: Clock
    end: Clock
    breaks: list[BreakWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> WorkDay:
        self.start, self.end = _truncate(self.start), _truncate(self.end)
        day_start, day_end = to_minutes(self.start), to_minutes(self.end)
        if day_start >= day_end:
            raise ValueError("errors.leave_schedule_day_invalid")

        # Sorted on the way in, so the overlap check is a single pass and every reader
        # (here, #48's compute_hours, the browser's live total) sees the same order.
        self.breaks.sort(key=lambda b: to_minutes(b.start))
        previous_end = day_start
        consumed = 0
        for window in self.breaks:
            start, end = to_minutes(window.start), to_minutes(window.end)
            if start < day_start or end > day_end:
                raise ValueError("errors.leave_schedule_break_outside")
            if start < previous_end:
                raise ValueError("errors.leave_schedule_breaks_overlap")
            previous_end = end
            consumed += end - start
        # A day whose breaks eat all of it is a mistake, not a zero-hour working day —
        # say so, rather than silently entitling the employee to nothing.
        if consumed >= day_end - day_start:
            raise ValueError("errors.leave_schedule_day_empty")
        return self


class WorkSchedule(BaseModel):
    """A week. ``None`` on a weekday means the employee does not work it."""

    mon: WorkDay | None = None
    tue: WorkDay | None = None
    wed: WorkDay | None = None
    thu: WorkDay | None = None
    fri: WorkDay | None = None
    sat: WorkDay | None = None
    sun: WorkDay | None = None

    def day(self, weekday: int) -> WorkDay | None:
        """The day for a ``date.weekday()`` index (0 = Monday)."""
        return getattr(self, WEEKDAYS[weekday])

    @model_validator(mode="after")
    def _not_empty(self) -> WorkSchedule:
        if all(getattr(self, key) is None for key in WEEKDAYS):
            raise ValueError("errors.leave_schedule_empty")
        return self


#: What a new org — and every employee without their own schedule — works.
#: ``08:30–17:00`` minus a ``12:30–13:00`` lunch = 8.0 h/day, 40 h/week.
_DEFAULT_DAY = {
    "start": "08:30",
    "end": "17:00",
    "breaks": [{"start": "12:30", "end": "13:00"}],
}
DEFAULT_SCHEDULE_JSON: dict = {
    **{key: dict(_DEFAULT_DAY) for key in ("mon", "tue", "wed", "thu", "fri")},
    "sat": None,
    "sun": None,
}


def default_schedule() -> WorkSchedule:
    return WorkSchedule.model_validate(DEFAULT_SCHEDULE_JSON)


def parse(raw: dict | None) -> WorkSchedule | None:
    return WorkSchedule.model_validate(raw) if raw else None


def dump(schedule: WorkSchedule) -> dict:
    """JSONB payload: ``{"mon": {"start": "08:30", …}, "sat": null, …}``."""
    return schedule.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Minute arithmetic — the single source of every leave hour
# --------------------------------------------------------------------------- #
def day_minutes(day: WorkDay | None, window: tuple[int, int] | None = None) -> int:
    """Worked minutes of ``day`` inside ``window`` (minutes-from-midnight), breaks removed.

    ``window`` is intersected with the scheduled block, which is exactly the clamp #48 asks
    for: "from 08:00" on an 08:30 day means "from the start of the day", not an error.
    """
    if day is None:
        return 0
    low, high = window or (0, MINUTES_PER_DAY)
    start = max(to_minutes(day.start), low)
    end = min(to_minutes(day.end), high)
    if end <= start:
        return 0
    worked = end - start
    for window_break in day.breaks:
        worked -= _overlap(
            start, end, to_minutes(window_break.start), to_minutes(window_break.end)
        )
    return max(0, worked)


def week_minutes(schedule: WorkSchedule) -> int:
    return sum(day_minutes(schedule.day(index)) for index in range(7))


def working_day_count(schedule: WorkSchedule) -> int:
    return sum(1 for index in range(7) if day_minutes(schedule.day(index)) > 0)


def to_hours(minutes: int) -> Decimal:
    """Minutes → hours, rounded to two decimals **once**."""
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def week_hours(schedule: WorkSchedule) -> Decimal:
    return to_hours(week_minutes(schedule))


def average_day_hours(schedule: WorkSchedule) -> Decimal:
    """The average **scheduled** working day — what "≈ 2 dagen" must divide by.

    Not ``hours_per_week / 5``: a three-day week is still made of 8-hour days, and telling a
    part-timer their 8 hours are "1,7 dagen" is a bug wearing a formula.
    """
    days = working_day_count(schedule)
    return to_hours(week_minutes(schedule) // days) if days else Decimal("0")
