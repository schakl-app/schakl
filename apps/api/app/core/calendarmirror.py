"""What a mirrored calendar event *says* — shared by every calendar the platform pushes into.

Approved leave, a freelancer's availability row and a planned task block each mirror outwards
(docs/GOOGLE.md §4, docs/MICROSOFT.md §4) through an outbox that snapshots the event at emit time
so the worker never re-reads another module's internals. The snapshot's *contents* — the title,
the per-day breakdown, the deep link, the RRULE cadence, the org's clock — are a rule about the
platform, not about the calendar being written into, so they are built once here and each
integration turns the same snapshot into its own wire shape (a Google event body, a Graph event).

Two integrations subscribing to the same bus events means a colleague who connected *both*
accounts gets the block in both calendars, which is the honest reading of "mirror my planning":
each is their own calendar, and choosing one for them is not a decision the platform can make.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.auth.sso import org_base_url
from app.core.models import Org, OrgSettings
from app.core.richtext import markdown_to_plaintext
from app.i18n import translate

LOCAL_TYPE_LEAVE = "leave_request"
LOCAL_TYPE_TASK_SCHEDULE = "task_schedule"
LOCAL_TYPE_AVAILABILITY = "availability"


async def user_locale(session: AsyncSession, org_id: uuid.UUID, user_id: Any) -> str | None:
    """The event lands on the *requester's* calendar, so their locale words it (#148); the org
    default is the fallback, like everywhere (§8)."""
    return await session.scalar(select(User.locale).where(User.id == user_id)) or (
        await session.scalar(select(OrgSettings.default_locale).where(OrgSettings.org_id == org_id))
    )


async def org_timezone(session: AsyncSession, org_id: uuid.UUID) -> str:
    from app.config import settings

    zone = await session.scalar(select(OrgSettings.timezone).where(OrgSettings.org_id == org_id))
    return zone or settings.default_timezone


def rrule(repeat_weeks: Any, repeat_until: Any, *, timed: bool) -> str | None:
    """A weekly RRULE for a rule-shaped row, or ``None`` for a one-off.

    A repeating availability row *is* a recurrence rule, so it mirrors as one event rather than
    as N — which is what keeps an edit an edit and a delete a delete instead of a diff against
    whatever the last horizon happened to place.

    ``UNTIL`` follows RFC 5545's typing rule: a DATE for an all-day series, a UTC DATE-TIME for a
    timed one. The timed form is stamped a **day late** on purpose — an occurrence at 17:00 local
    in a zone behind UTC falls after 23:59:59Z of its own date, so the honest bound would drop
    the last occurrence. A cadence is at least a week, so a day of slack can never let an extra
    one in.
    """
    if not repeat_weeks:
        return None
    rule = f"RRULE:FREQ=WEEKLY;INTERVAL={int(repeat_weeks)}"
    if repeat_until:
        end = date.fromisoformat(str(repeat_until))
        if timed:
            rule += f";UNTIL={(end + timedelta(days=1)).strftime('%Y%m%d')}T235959Z"
        else:
            rule += f";UNTIL={end.strftime('%Y%m%d')}"
    return rule


def is_timed(payload: dict[str, Any]) -> bool:
    """Timed within one day, else an all-day span — the one split every calendar makes."""
    return bool(
        payload.get("start_time")
        and payload.get("end_time")
        and payload.get("start_date") == payload.get("end_date")
    )


# --------------------------------------------------------------------------- #
# Snapshots — what the outbox row carries
# --------------------------------------------------------------------------- #
async def leave_snapshot(
    session: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any], user_id: Any
) -> dict[str, Any]:
    locale = await user_locale(session, org_id, user_id)
    return {
        "summary": await leave_summary(session, org_id, payload, locale),
        "description": leave_description(payload, locale),
        "local_type": LOCAL_TYPE_LEAVE,
        "local_id": str(payload["leave_request_id"]),
        "start_date": str(payload["start_date"]),
        "end_date": str(payload["end_date"]),
        "start_time": str(payload["start_time"]) if payload.get("start_time") else None,
        "end_time": str(payload["end_time"]) if payload.get("end_time") else None,
        "timezone": await org_timezone(session, org_id),
    }


async def availability_snapshot(
    session: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any], user_id: Any
) -> dict[str, Any]:
    """A freelancer's availability exception.

    What is mirrored is the **row**, not the day it resolves to — the resolved day is the base
    week bent by exceptions and no calendar has a base week, so pushing the resolution would mean
    pushing every ordinary working day too. One row, one event, and a repeat travels as an RRULE.

    The two kinds differ in exactly one more way, and it is the useful one: an ``unavailable``
    day is **busy** (that is what it says), while an ``extra`` day is **free** — a day somebody
    offers to work is not a booking, and mirroring it as busy would block the very hours it
    exists to advertise.
    """
    locale = await user_locale(session, org_id, user_id)
    unavailable = payload.get("kind") == "unavailable"
    summary = translate(
        "calendar.mirror.availability_unavailable"
        if unavailable
        else "calendar.mirror.availability_available",
        locale,
    )
    return {
        "summary": summary,
        "description": payload.get("note") or "",
        "local_type": LOCAL_TYPE_AVAILABILITY,
        "local_id": str(payload["availability_id"]),
        "start_date": str(payload["date"]),
        "end_date": str(payload["date"]),
        "start_time": payload.get("start_time"),
        "end_time": payload.get("end_time"),
        "repeat_weeks": payload.get("repeat_weeks"),
        "repeat_until": payload.get("repeat_until"),
        "transparency": "opaque" if unavailable else "transparent",
        "timezone": await org_timezone(session, org_id),
    }


async def task_schedule_snapshot(
    session: AsyncSession, org: Org, payload: dict[str, Any], user_id: Any
) -> dict[str, Any]:
    locale = await user_locale(session, org.id, user_id)
    return {
        "summary": task_summary(payload.get("task_title"), payload.get("company_name"), locale),
        "description": task_description(org, payload, locale),
        "local_type": LOCAL_TYPE_TASK_SCHEDULE,
        "local_id": str(payload["schedule_id"]),
        "start_date": str(payload["start_date"]),
        "end_date": str(payload["end_date"]),
        "start_time": str(payload["start_time"]) if payload.get("start_time") else None,
        "end_time": str(payload["end_time"]) if payload.get("end_time") else None,
        "timezone": payload.get("timezone") or await org_timezone(session, org.id),
    }


# --------------------------------------------------------------------------- #
# Wording
# --------------------------------------------------------------------------- #
def task_summary(title: str | None, company_name: str | None, locale: str | None) -> str:
    """"Nova Fietsen: Redesign homepage" — the client's name and the task's title.

    The client leads because that is what a glance at a week wants to know: whose work sits
    where. The old marker ("Taak: …") said what *kind* of record the block was, which a calendar
    full of them already says, and is kept only for a task with no client — an internal job —
    where there is nothing else to lead with.
    """
    if company_name and title:
        return f"{company_name}: {title}"
    if company_name:
        return company_name
    base = translate("calendar.mirror.task_event_title", locale)
    return f"{base}: {title}" if title else base


def task_description(org: Org, payload: dict[str, Any], locale: str | None) -> str:  # noqa: ARG001
    """The task's own description (flattened from markdown) plus a direct deeplink to the task —
    a calendar event has no URL field of its own, so the link lives in the notes text (#188)."""
    parts: list[str] = []
    desc = payload.get("task_description")
    if desc:
        parts.append(markdown_to_plaintext(desc))
    parts.append(f"{org_base_url(org)}/tasks/{payload['task_id']}")
    return "\n\n".join(parts)


async def leave_summary(
    session: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any], locale: str | None
) -> str:
    """"Verlof: Vakantie", never a bare "Verlof" (#148). The tenant's own type label
    (``label_i18n``) is read with org-scoped SQL — the mirror never imports leave internals."""
    base = translate("calendar.mirror.leave_event_title", locale)
    type_id = payload.get("leave_type_id")
    if not type_id:
        return base
    label_i18n = await session.scalar(
        text("SELECT label_i18n FROM leave_types WHERE id = :tid AND org_id = :oid"),
        {"tid": type_id, "oid": org_id},
    )
    if not isinstance(label_i18n, dict):
        return base
    label = label_i18n.get(locale or "") or label_i18n.get("nl") or label_i18n.get("en")
    if not label:
        label = next(iter(label_i18n.values()), None)
    return f"{base}: {label}" if label else base


def leave_description(payload: dict[str, Any], locale: str | None) -> str:
    """The per-day breakdown, one line per working day (#148) — a calendar shows a multi-day
    all-day span without saying which day costs what; this does."""
    lines = [
        translate(
            "calendar.mirror.leave_event_day",
            locale,
            date=_european_day(row["date"]),
            hours=f"{row['hours']:g}",
        )
        for row in payload.get("breakdown") or []
    ]
    return "\n".join(lines)


def _european_day(iso_day: str) -> str:
    year, month, day = iso_day.split("-")
    return f"{day}-{month}-{year}"
