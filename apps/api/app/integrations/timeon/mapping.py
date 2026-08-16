"""Translating between a Timeon hour row and a schakl ``TimeEntry``, and deciding when they
disagree. Business-licensed — see LICENSE.

This file holds the two things the sync is actually *about*: what a field means on each side,
and what counts as a change. Everything else is plumbing around it.

**Comparison happens in a neutral shape, never field-by-field across the two vocabularies.**
:func:`neutral_from_entry` and :func:`neutral_from_row` both answer the same seven keys, and
:func:`fingerprint` hashes that. It is what makes "did this side move" a one-line question, and
what keeps the *conflict screen* naming the seven things a person can actually reason about
rather than the eighty fields Timeon ships (``secondsBillable``, ``weekdayStringShort``,
``totalRevenueString``) — #300's rule: present, never dump the row.

Four mapping decisions carry a reason, and all four were learned from real data rather than
reasoned about.

**A time entry's clock is the wall clock somebody typed.** ``TimeEntry.started_at`` is
``TIMESTAMPTZ`` and holds that wall clock *as UTC* (CLAUDE.md §8 — stored instants are UTC,
date-only and clock-only values stay wall-clock). Converting Europe/Amsterdam → UTC here would
shift every historical timesheet by an hour and make every row look changed on the first run.

**``breakSeconds`` is not a lunch break** and is dropped in both directions. It is
``(to − from) − seconds`` — the *unbooked remainder* of the window, verified in 324 of the 325
rows that carry one (the exception being corrupt: a six-minute window reporting a 165-hour
break). Feeding it into ``break_minutes`` would push an entry's end hours past the work and blow
schakl's 24-hour cap; carrying schakl's real break out to it would mean writing a derived field
as if it were a stored one.

**605 of 2823 rows carry no start time at all.** They are placed deterministically — stacked
from 09:00 in ``hourID`` order, exactly as the migration importer placed them, so an entry it
already wrote adopts without moving — and the placed value is then **excluded from the
fingerprint**. Including it would mean that deleting one morning row silently re-timed every
later row on that person's day and reported six rows of drift about a change nobody made.

**An unresolvable reference is a sentinel, not a difference.** A schakl project with no Timeon
pairing and a Timeon project with no schakl pairing both canonicalise to ``"?"``. The
alternative — comparing an id against nothing — reports drift on every run for a difference the
sync is not configured to fix, which is exactly the queue nobody reads. The *pairing* gap is
reported once per run as a warning, which is the thing an admin can act on.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

#: Where a start-less Timeon row's day begins. The migration importer's constant, kept
#: identical so its 2814 entries adopt in place rather than being re-timed on first contact.
DEFAULT_START_SECONDS = 9 * 3600

#: What the sync compares, and therefore what a conflict may be about. Everything else Timeon
#: sends is display, derived, or something schakl has no field for.
COMPARED_FIELDS = ("started_on", "start_seconds", "minutes", "project", "company", "description",
                   "billable")

#: A reference that exists on one side and is paired on neither. See the module docstring.
UNRESOLVED = "?"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_timeon_ts(value: str | None) -> datetime | None:
    """Timeon writes ``2026-08-04T15:15:37.07`` with no zone. Read as UTC, like every other
    wall-clock value here (see the module docstring)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).split(".")[0]).replace(tzinfo=UTC)
    except ValueError:
        return None


def row_date(row: dict[str, Any]) -> date | None:
    """The calendar day a Timeon hour row belongs to."""
    raw = row.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def plan_start_seconds(rows: list[dict[str, Any]]) -> dict[int, int]:
    """A start-of-day second for every row, including the ones that carry none.

    Start-less rows are stacked after the previous one on that person's day from 09:00, so a
    pulled timesheet reads plausibly instead of piling six entries onto one instant. Placement
    depends **only** on the Timeon data and a stable sort — never on what schakl already holds —
    which is what makes it reproducible across runs; and because ``hourID`` increases
    monotonically, appending a new row never moves an existing one.

    The window handed in must be at least the whole day, or a row's neighbours are invisible and
    it stacks from 09:00 again. The sync always reads whole months, so it is.
    """
    placed: dict[int, int] = {}
    per_day: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        per_day[(row.get("userID"), str(row.get("date") or "")[:10])].append(row)
    for day_rows in per_day.values():
        cursor = DEFAULT_START_SECONDS
        for row in sorted(
            day_rows,
            key=lambda r: (r.get("fromSeconds") is None, int(r.get("hourID") or 0)),
        ):
            hour_id = int(row.get("hourID") or 0)
            if row.get("fromSeconds") is not None:
                placed[hour_id] = int(row["fromSeconds"])
            else:
                placed[hour_id] = cursor
                cursor += int(row.get("seconds") or 0)
    return placed


def started_at_for(day: date, start_seconds: int) -> datetime:
    """The instant a pulled entry starts at — the wall clock, stored as UTC (§8)."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(seconds=start_seconds)


def start_seconds_of(started_at: datetime) -> int:
    """Seconds since that entry's own midnight. The inverse of :func:`started_at_for`."""
    local = started_at.astimezone(UTC)
    return local.hour * 3600 + local.minute * 60 + local.second


class Resolver:
    """The two id dictionaries every translation needs, in one object.

    Built once per run from ``timeon_links``, never per row: resolving "which schakl project is
    Timeon 2115429" with a query per hour is the shape docs/PERFORMANCE.md exists to prevent, and
    a 400-row window would issue 1200 of them.
    """

    def __init__(
        self,
        *,
        users: dict[str, uuid.UUID],
        companies: dict[str, uuid.UUID],
        projects: dict[str, uuid.UUID],
    ) -> None:
        self.user_by_ext = users
        self.company_by_ext = companies
        self.project_by_ext = projects
        self.ext_by_user = {v: k for k, v in users.items()}
        self.ext_by_company = {v: k for k, v in companies.items()}
        self.ext_by_project = {v: k for k, v in projects.items()}


def neutral_from_row(
    row: dict[str, Any], *, start_seconds: int | None, resolver: Resolver
) -> dict[str, Any]:
    """One Timeon hour row, in the shape both sides are compared in.

    ``start_seconds`` is what :func:`plan_start_seconds` decided; ``None`` there means the row
    carried no start of its own, which is recorded as ``start_seconds: None`` so the placed value
    never enters the fingerprint.
    """
    day = row_date(row)
    project_ext = _stringify(row.get("projectID"))
    company_ext = _stringify(row.get("customerID"))
    return {
        "started_on": day.isoformat() if day else "",
        "start_seconds": start_seconds if row.get("fromSeconds") is not None else None,
        "minutes": int(row.get("seconds") or 0) // 60,
        "project": (
            UNRESOLVED
            if project_ext and project_ext not in resolver.project_by_ext
            else project_ext
        ),
        "company": (
            UNRESOLVED
            if company_ext and company_ext not in resolver.company_by_ext
            else company_ext
        ),
        "description": (row.get("remark") or "").strip(),
        "billable": bool(row.get("billable")),
    }


def neutral_from_entry(entry: Any, *, resolver: Resolver, has_remote_start: bool) -> dict[str, Any]:
    """One schakl ``TimeEntry``, in the same shape.

    ``has_remote_start`` mirrors the rule above from the other end: when the paired Timeon row
    carries no start of its own, this entry's start is a value *we* placed and comparing it would
    report our own arithmetic as somebody's edit.
    """
    started = entry.started_at.astimezone(UTC)
    project_ext = resolver.ext_by_project.get(entry.project_id) if entry.project_id else None
    company_ext = resolver.ext_by_company.get(entry.company_id) if entry.company_id else None
    return {
        "started_on": started.date().isoformat(),
        "start_seconds": start_seconds_of(started) if has_remote_start else None,
        "minutes": int(entry.minutes or 0),
        "project": (
            "" if entry.project_id is None else (project_ext or UNRESOLVED)
        ),
        "company": (
            "" if entry.company_id is None else (company_ext or UNRESOLVED)
        ),
        "description": (entry.description or "").strip(),
        "billable": bool(entry.billable),
    }


def fingerprint(neutral: dict[str, Any]) -> str:
    """A stable digest of a neutral shape.

    ``sort_keys`` because a dict's order is not part of what it means, and because a JSONB
    round-trip does not preserve one anyway (#373: Postgres orders JSONB keys by length then
    bytes, which is how a carefully ordered payload came back scrambled).
    """
    payload = json.dumps(
        {k: neutral.get(k) for k in COMPARED_FIELDS}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def differences(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Only the compared fields that actually differ, in schakl's vocabulary on both sides.

    Empty when the two agree — and *agreement is this function*, never equality of the two
    fingerprints, because :data:`UNRESOLVED` is a sentinel and comparing it as if it were a value
    is what the module docstring's fourth rule exists to prevent. Producing the sentinel and then
    hashing it made every pulled row whose Timeon project has no pairing here differ from the row
    the pull had *just written*: ``project`` read ``"?"`` on one side against ``""`` on the other,
    62 of 66 rows on the first real run. They then landed in the "allowed to differ" arm — the
    state a **dismissed conflict** leaves behind — so a decision nobody made was recorded 62
    times, and the one signal that arm carries was buried under it.

    So a field where either side is ``UNRESOLVED`` is not a difference. The rule is not "these
    are probably the same": it is that **no direction of sync could act on it**. Pulling cannot
    set a project schakl has never heard of and pushing cannot name one Timeon has never heard
    of, so the only thing left to do with the difference is report it — which the run already
    does, once, as the ``project_unmapped`` / ``customer_unmapped`` warning an admin can actually
    close. Reporting it a second time per *row*, forever, is the queue nobody reads.

    The moment the pairing appears, the sentinel becomes a real id, the fingerprint moves, and
    that side reads as changed on the very next run — which is exactly right, and is why the
    hashes stay one-sided (:func:`fingerprint` answers "did *this* side move", never "do the two
    agree").
    """
    out: dict[str, dict[str, Any]] = {}
    for field in COMPARED_FIELDS:
        left, right = local.get(field), remote.get(field)
        if left == right:
            continue
        if left == UNRESOLVED or right == UNRESOLVED:
            continue
        out[field] = {"local": left, "remote": right}
    return out


def natural_key(
    user_id: uuid.UUID,
    started_at: datetime,
    minutes: int,
    project_id: uuid.UUID | None,
    description: str | None,
) -> tuple[Any, ...]:
    """What makes an entry recognisably itself when nothing is paired yet.

    Byte-identical to the migration importer's key (``docs/TIMEON.md`` §5), and that is the
    point: adoption's whole job is to recognise the 2814 entries that importer already wrote and
    pair them *without writing anything*. A key that merely looked similar would pair none of
    them and the first sync would import the entire history a second time.

    Measured unique for 2822 of 2823 real rows; the single collision is a pair of identical
    two-hour entries with no remark, which adoption handles by pairing them in a stable order
    rather than by pretending the key is injective.
    """
    return (
        user_id,
        started_at.astimezone(UTC).replace(tzinfo=None),
        int(minutes),
        project_id,
        (description or "").strip(),
    )


def timeon_payload(
    *,
    hour_id: int | None,
    observed: dict[str, Any],
    user_ext: str,
    company_ext: str | None,
    project_ext: str | None,
    day: date,
    start_seconds: int | None,
    minutes: int,
    description: str | None,
    billable: bool,
) -> dict[str, Any]:
    """The body a push sends to ``hour/save``.

    **Whole-row, always.** That endpoint replaces rather than patches — a save carrying
    ``{hourID, seconds}`` was measured to blank the remark and null out both ``projectID`` and
    ``customerID`` — so anything schakl does not own is carried over verbatim from what we last
    observed rather than left out. ``distance``, the expense fields and the category are exactly
    that: real data on Timeon's side that schakl has no field for, and dropping them would be an
    integration quietly deleting a client's records as a side effect of correcting a description.
    """
    payload: dict[str, Any] = {
        "userID": int(user_ext),
        "date": f"{day.isoformat()}T00:00:00",
        "seconds": int(minutes) * 60,
        "remark": (description or ""),
        "billable": bool(billable),
    }
    if hour_id is not None:
        payload["hourID"] = int(hour_id)
    if company_ext:
        payload["customerID"] = int(company_ext)
    if project_ext:
        payload["projectID"] = int(project_ext)
    if start_seconds is not None:
        payload["fromSeconds"] = int(start_seconds)
    # Carried, never authored. Absent from `observed` on a create, which is correct — there is
    # nothing over there to preserve yet.
    for carried in (
        "taskID",
        "categoryID",
        "contactpersonID",
        "distance",
        "distanceCategoryID",
        "expenseCategoryID",
        "expenseValue",
        "internalRemark",
        "rateID",
    ):
        if observed.get(carried) not in (None, ""):
            payload[carried] = observed[carried]
    return payload


#: The subset of a Timeon hour row worth storing on a link. Eighty keys arrive; these are the
#: ones a push has to carry back (see :func:`timeon_payload`) plus the ones a conflict screen
#: renders. Storing the whole row would put ``totalRevenueString`` in a JSONB column on every
#: one of three thousand links for the benefit of nobody.
OBSERVED_FIELDS = (
    "hourID",
    "userID",
    "customerID",
    "projectID",
    "taskID",
    "categoryID",
    "contactpersonID",
    "date",
    "fromSeconds",
    "seconds",
    "billable",
    "remark",
    "internalRemark",
    "approved",
    "approvedOn",
    "approvedBy",
    "invoiceID",
    "deleted",
    "distance",
    "distanceCategoryID",
    "expenseCategoryID",
    "expenseValue",
    "rateID",
    "project",
    "customer",
    "user",
)


def observed_of(row: dict[str, Any]) -> dict[str, Any]:
    """What of a Timeon row we keep. See :data:`OBSERVED_FIELDS`."""
    return {k: row.get(k) for k in OBSERVED_FIELDS if row.get(k) is not None}
