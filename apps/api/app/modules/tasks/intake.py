"""A task arrives by e-mail (``taak@bureau.nl``) — the tasks module's half of the intake seam.

The mailbox feeds recognise the address and hand one normalised
:class:`~app.core.mailbox.intake.IntakeMessage` to :func:`handle_intake_message`; this module
turns it into a task, or parks it for its sender. The rules, in the order they run:

1. **The sender is a colleague, or nothing is stored.** ``sender_user_id`` came off the feed's
   own "who counts as us" map; ``None`` is refused before a row exists — the address is
   reachable from the whole internet, and an outsider's mail must not become a record.
2. **One mail is one act.** The receipt row is inserted first, and the partial unique index on
   the RFC-822 id decides who was first (docs/PAYMENTS.md). A second copy answers with the first
   copy's links, so the feed can still file its interaction half onto the same task.
3. **The sender is the actor.** A :func:`~app.core.principal.member_context` is built for them,
   and the task is refused where they would have been refused: no ``tasks.task.create``, a
   client outside their company horizon (#285), a portal login.
4. **The sender's words outrank everything.** A directive line (``klant:``, ``voor:``,
   ``deadline:``, ``project:``, ``labels:``, ``prioriteit:``) or a ``[Klant]`` in the subject
   decides its field. Then the addresses in the forwarded block, resolved through the same
   contact match and ranking the feeds use (#305), name the client when they name exactly one.
   Then the model (``intake_ai``) fills what is still blank — never what is not.
5. **A missing client parks; a missing deadline defaults.** #391/#392 one door over: a
   deadline has an honest default (today + the org's setting), a client does not, so the mail
   waits in ``/tasks/inbox`` for the person who sent it, with everything it carried.

The sender hears the outcome through the ordinary notification system — bell, mail, digest,
by their own preferences — with the client, the assignee and the deadline in the sentence, and
a note of which of them the model chose.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EmitContext, emit
from app.core.mailbox.intake import IntakeAddress, IntakeMessage, IntakeOutcome
from app.core.mailbox.internals import match_contacts
from app.core.mailbox.matching import participants_from_addresses, resolve_mappings
from app.core.members import staff_select
from app.core.principal import member_context
from app.core.storage import system as storage_system
from app.core.storage.models import StoredFile
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.modules.tasks import intake_ai
from app.modules.tasks.intake_ai import IntakePlan
from app.modules.tasks.models import (
    Task,
    TaskIntakeMessage,
    TaskIntakeStatus,
    TaskLabel,
    TaskPriority,
    TaskSettings,
)
from app.modules.tasks.schemas import (
    TaskCreate,
    TaskIntakeComplete,
    TaskIntakeRead,
    TaskIntakeSummary,
    TaskSettingsRead,
    TaskSettingsUpdate,
)
from app.modules.tasks.system import (
    TaskEnrichment,
    _untrusted_markdown,
    apply_ai_enrichment_system,
    create_task_system,
    record_ai_activity_system,
    set_task_labels_system,
)

logger = logging.getLogger("schakl.tasks.intake")

INTAKE_KEY = "tasks"
ENTITY_TYPE = "task_intake"
#: Notification events (registered in ``notifications/events.py``).
CREATED_EVENT = "task.intake_created"
PARKED_EVENT = "task.intake_parked"

#: What a parked row's ``reason`` may say. i18n: ``tasks.intake.reason.<value>``.
REASON_NO_CLIENT = "no_client"
REASON_AMBIGUOUS_CLIENT = "ambiguous_client"
REASON_NO_PERMISSION = "no_permission"
REASON_OUTSIDE_HORIZON = "outside_horizon"

MAX_DESCRIPTION_CHARS = 20_000

# --------------------------------------------------------------------------- #
# The address provider (read by core once per poll)
# --------------------------------------------------------------------------- #


async def intake_addresses_for_org(session: AsyncSession, org_id: uuid.UUID) -> list[IntakeAddress]:
    address = await session.scalar(
        select(TaskSettings.intake_address).where(TaskSettings.org_id == org_id)
    )
    if not address:
        return []
    return [IntakeAddress(email=address.lower(), key=INTAKE_KEY)]


# --------------------------------------------------------------------------- #
# Parsing — the sender's own words, deterministically
# --------------------------------------------------------------------------- #

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:fwd?|fw|re|aw|tr|wg|doorst\.?|antw)\s*:\s*)+", re.I)
_SUBJECT_CLIENT_RE = re.compile(r"^\s*\[([^\]]{1,120})\]\s*(.*)$", re.S)

_DIRECTIVES: dict[str, tuple[str, ...]] = {
    "client": ("klant", "client", "company", "bedrijf", "opdrachtgever"),
    "assignee": ("voor", "for", "assignee", "toewijzen", "assign", "aan"),
    "due": ("deadline", "due", "datum", "date", "voor op", "uiterlijk"),
    "project": ("project",),
    "labels": ("labels", "label", "tags", "tag"),
    "priority": ("prioriteit", "priority", "prio"),
}
_DIRECTIVE_BY_WORD: dict[str, str] = {
    word: key for key, words in _DIRECTIVES.items() for word in words
}
_DIRECTIVE_RE = re.compile(r"^\s*(?:[-*•]\s*)?\**([a-zA-Z ]{2,14})\**\s*[:=]\s*(.+?)\s*$")

#: Where the colleague stops and somebody else's mail begins. Gmail, Outlook and Apple Mail
#: each mark a forward and a reply differently, and the HTML→markdown conversion keeps the
#: words but not the wrapper — so the split is on the words.
_FORWARD_MARKERS = (
    re.compile(r"^\s*-{3,}\s*(forwarded message|doorgestuurd bericht)\s*-{3,}", re.I),
    re.compile(
        r"^\s*-{3,}\s*(original message|oorspronkelijk bericht|ursprüngliche nachricht)", re.I
    ),
    re.compile(r"^\s*(?:\*\*)?(from|van|de)(?:\*\*)?\s*:\s*.+@.+", re.I),
    # "On Tue, 1 Sep 2026 at 10:00, Klant <k@client.nl> wrote:" and Gmail's Dutch
    # "Op di 1 sep 2026 om 10:00 schreef Klant <k@client.nl>:" — the verb sits at either end.
    re.compile(r"^\s*(on|op)\s.+\b(wrote|schreef)\b.*:\s*$", re.I),
    re.compile(r"^\s*begin forwarded message", re.I),
    re.compile(r"^\s*>"),
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_WEEKDAYS = {
    "maandag": 0, "monday": 0, "ma": 0, "mon": 0,
    "dinsdag": 1, "tuesday": 1, "di": 1, "tue": 1,
    "woensdag": 2, "wednesday": 2, "wo": 2, "wed": 2,
    "donderdag": 3, "thursday": 3, "do": 3, "thu": 3,
    "vrijdag": 4, "friday": 4, "vr": 4, "fri": 4,
    "zaterdag": 5, "saturday": 5, "za": 5, "sat": 5,
    "zondag": 6, "sunday": 6, "zo": 6, "sun": 6,
}  # fmt: skip
_PRIORITY_WORDS = {
    "laag": TaskPriority.LOW.value,
    "low": TaskPriority.LOW.value,
    "normaal": TaskPriority.NORMAL.value,
    "normal": TaskPriority.NORMAL.value,
    "hoog": TaskPriority.HIGH.value,
    "high": TaskPriority.HIGH.value,
    "urgent": TaskPriority.HIGH.value,
    "spoed": TaskPriority.HIGH.value,
}


@dataclass
class IntakeDraft:
    """What the mail says on its own, before any lookup."""

    title: str
    client_hint: str | None = None
    assignee_hint: str | None = None
    due_hint: str | None = None
    project_hint: str | None = None
    label_hints: list[str] = field(default_factory=list)
    priority: str | None = None
    #: The colleague's own words, directives removed.
    own_text: str = ""
    #: What they forwarded or quoted — somebody else's words.
    forwarded_text: str = ""
    #: The whole body, directives removed — what the task's description carries.
    body: str = ""
    #: Addresses found in the forwarded part, lower-cased, in order of appearance.
    addresses: list[str] = field(default_factory=list)


def clean_subject(subject: str | None) -> tuple[str, str | None]:
    """``("title", client hint)`` — the reply/forward prefixes stripped, a leading ``[Klant]``
    read as the client and taken off the title."""
    raw = (subject or "").strip()
    previous = None
    while previous != raw:
        previous = raw
        raw = _SUBJECT_PREFIX_RE.sub("", raw).strip()
    match = _SUBJECT_CLIENT_RE.match(raw)
    if match and match.group(2).strip():
        return match.group(2).strip()[:512], match.group(1).strip()
    return raw[:512], None


def split_forward(body: str) -> tuple[str, str]:
    """``(own, forwarded)`` at the first line that reads as a forward or quote header."""
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if any(marker.match(line) for marker in _FORWARD_MARKERS):
            return "\n".join(lines[:index]).strip(), "\n".join(lines[index:]).strip()
    return body.strip(), ""


def _read_directives(own: str) -> tuple[dict[str, str], str]:
    """Directive lines out of the colleague's part, and the part without them."""
    found: dict[str, str] = {}
    kept: list[str] = []
    for line in own.splitlines():
        match = _DIRECTIVE_RE.match(line)
        if match:
            key = _DIRECTIVE_BY_WORD.get(match.group(1).strip().lower())
            if key is not None and key not in found:
                found[key] = match.group(2).strip()
                continue
        kept.append(line)
    return found, "\n".join(kept).strip()


def parse_intake(subject: str | None, body: str | None) -> IntakeDraft:
    title, client_hint = clean_subject(subject)
    own, forwarded = split_forward(body or "")
    directives, own_clean = _read_directives(own)
    draft = IntakeDraft(
        title=title or "",
        client_hint=directives.get("client") or client_hint,
        assignee_hint=directives.get("assignee"),
        due_hint=directives.get("due"),
        project_hint=directives.get("project"),
        label_hints=[
            part.strip()
            for part in re.split(r"[,;]", directives.get("labels", ""))
            if part.strip()
        ],
        priority=_PRIORITY_WORDS.get((directives.get("priority") or "").strip().lower()),
        own_text=own_clean,
        forwarded_text=forwarded,
        body="\n\n".join(part for part in (own_clean, forwarded) if part).strip(),
        addresses=list(dict.fromkeys(a.lower() for a in _EMAIL_RE.findall(forwarded))),
    )
    if not draft.title:
        first_line = next((ln.strip() for ln in own_clean.splitlines() if ln.strip()), "")
        draft.title = first_line[:120]
    return draft


def parse_due(value: str | None, *, today: date) -> date | None:
    """A deadline as people type it: ISO, European, a weekday, ``morgen``, ``+3``, ``over 2
    weken``. ``None`` for anything it does not recognise — a guess is worse than the default."""
    if not value:
        return None
    raw = value.strip().lower().rstrip(".")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", raw)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        return candidate if candidate >= today else date(today.year + 1, month, day)
    if raw in {"vandaag", "today", "nu", "now"}:
        return today
    if raw in {"morgen", "tomorrow"}:
        return today + timedelta(days=1)
    if raw in {"overmorgen"}:
        return today + timedelta(days=2)
    match = re.fullmatch(r"\+?\s*(\d{1,3})\s*(d|dagen|days?)?", raw)
    if match:
        return today + timedelta(days=int(match.group(1)))
    match = re.fullmatch(r"(?:over|in)\s+(\d{1,2})\s+(dag|dagen|day|days|week|weken|weeks?)", raw)
    if match:
        count = int(match.group(1))
        unit = match.group(2)
        return today + timedelta(days=count * (7 if unit.startswith("w") else 1))
    if raw in {"volgende week", "next week"}:
        return today + timedelta(days=7)
    if raw in {"eind van de week", "end of week", "end of the week", "deze week", "this week"}:
        return today + timedelta(days=(4 - today.weekday()) % 7)
    words = raw.replace("volgende ", "next ").replace("aanstaande ", "").replace("a.s. ", "")
    # "volgende week woensdag": the Wednesday of the week after this one. "volgende woensdag":
    # the next Wednesday strictly after today. "woensdag": the coming one, today included.
    next_week = words.startswith("next week ")
    wants_next = words.startswith("next ")
    name = words.removeprefix("next ").removeprefix("week ").strip()
    weekday = _WEEKDAYS.get(name)
    if weekday is None:
        return None
    if next_week:
        return today - timedelta(days=today.weekday()) + timedelta(days=7 + weekday)
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and wants_next:
        ahead = 7
    return today + timedelta(days=ahead)


# --------------------------------------------------------------------------- #
# Resolution — the sender's words against the org's records, under the sender's horizon
# --------------------------------------------------------------------------- #


@dataclass
class Resolved:
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    assignee_user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    due_date: date | None = None
    label_ids: list[uuid.UUID] = field(default_factory=list)
    #: Which fields the model decided (``company``, ``assignee``, ``due_date``, ``project``).
    by_model: list[str] = field(default_factory=list)
    reason: str | None = None


def _horizon_clause(actor: RequestContext) -> tuple[str, dict[str, Any]]:
    if actor.company_scope is None:
        return "", {}
    return " AND id = ANY(:scope)", {"scope": list(actor.company_scope)}


async def _company_by_name(actor: RequestContext, hint: str) -> tuple[uuid.UUID | None, str | None]:
    """Exactly one live client whose label or legal name is the hint (or contains it, when only
    one does). ``(None, reason)`` otherwise — ambiguity is a reason, not a coin toss."""
    needle = hint.strip().lower()
    if not needle:
        return None, REASON_NO_CLIENT
    clause, params = _horizon_clause(actor)
    base = (
        "SELECT id, name FROM companies WHERE org_id = :oid AND deleted_at IS NULL "
        "AND status <> 'archived'" + clause
    )
    exact = (
        await actor.session.execute(
            text(base + " AND (lower(name) = :needle OR lower(legal_name) = :needle) LIMIT 3"),
            {"oid": actor.org.id, "needle": needle, **params},
        )
    ).all()
    rows = exact
    if not rows:
        rows = (
            await actor.session.execute(
                text(base + " AND (name ILIKE :like OR legal_name ILIKE :like) LIMIT 3"),
                {"oid": actor.org.id, "like": f"%{needle}%", **params},
            )
        ).all()
    if len(rows) == 1:
        return rows[0][0], rows[0][1]
    return None, (REASON_AMBIGUOUS_CLIENT if len(rows) > 1 else REASON_NO_CLIENT)


async def _company_by_addresses(
    actor: RequestContext, message: IntakeMessage, addresses: list[str]
) -> tuple[uuid.UUID | None, str | None]:
    """The client the forwarded mail's people belong to — the feeds' own match and ranking, so
    an agency address in the thread does not claim it (#305). One client, or a reason."""
    if not addresses or message.internals is None:
        return None, None
    internals = message.internals
    outsiders = [a for a in addresses if a not in internals.ours]
    if not outsiders:
        return None, None
    participants = participants_from_addresses(
        sender=(None, outsiders[0]), to=[(None, a) for a in outsiders[1:]], cc=[]
    )
    matches = await match_contacts(actor.session, actor.org.id, participants, internals)
    companies: set[uuid.UUID] = set()
    for match in matches:
        if match.is_staff:
            continue
        for company_id in match.company_ids:
            if company_id not in internals.company_ids:
                companies.add(company_id)
    if not companies:
        return None, None
    if len(companies) > 1:
        # A thread across two clients names no single one. The ranking would pick one
        # deterministically for a *timeline* row, which is remappable; a task is not.
        return None, REASON_AMBIGUOUS_CLIENT
    ranked = resolve_mappings(matches, internal_company_ids=internals.company_ids)
    company_id = ranked.get("company_id") or next(iter(companies))
    if actor.company_scope is not None and company_id not in actor.company_scope:
        return None, REASON_OUTSIDE_HORIZON
    return company_id, None


async def _company_name(
    session: AsyncSession, org_id: uuid.UUID, company_id: uuid.UUID
) -> str | None:
    return await session.scalar(
        text("SELECT name FROM companies WHERE org_id = :oid AND id = :cid"),
        {"oid": org_id, "cid": company_id},
    )


async def _member_by_hint(
    actor: RequestContext, hint: str, *, sender_id: uuid.UUID
) -> uuid.UUID | None:
    """One active colleague the hint names: their address, their full name, or a unique
    first-name / prefix match. ``mij``/``me`` is the sender."""
    needle = hint.strip().lower().lstrip("@")
    if not needle:
        return None
    if needle in {"mij", "me", "ik", "myself", "mezelf"}:
        return sender_id
    result = await actor.session.execute(staff_select(actor.org.id, active_only=True))
    rows = result.scalars().all()
    by_email = [u for u in rows if (u.email or "").lower() == needle]
    if len(by_email) == 1:
        return by_email[0].id
    exact = [u for u in rows if (u.full_name or "").strip().lower() == needle]
    if len(exact) == 1:
        return exact[0].id
    prefix = [
        u
        for u in rows
        if (u.full_name or "").strip().lower().startswith(needle)
        or any(part.startswith(needle) for part in (u.full_name or "").lower().split())
        or (u.email or "").lower().split("@")[0] == needle
    ]
    if len(prefix) == 1:
        return prefix[0].id
    return None


async def _project_by_hint(
    actor: RequestContext, hint: str, company_id: uuid.UUID | None
) -> uuid.UUID | None:
    needle = hint.strip().lower()
    if not needle or company_id is None:
        return None
    rows = (
        await actor.session.execute(
            text(
                "SELECT id FROM projects WHERE org_id = :oid AND company_id = :cid "
                "AND status <> 'archived' AND (lower(name) = :needle OR name ILIKE :like) "
                "ORDER BY (lower(name) = :needle) DESC LIMIT 2"
            ),
            {"oid": actor.org.id, "cid": company_id, "needle": needle, "like": f"%{needle}%"},
        )
    ).all()
    if len(rows) == 1 or (rows and len(rows) > 1):
        return rows[0][0]
    return None


async def _labels_by_hints(actor: RequestContext, hints: list[str]) -> list[uuid.UUID]:
    if not hints:
        return []
    wanted = {h.strip().lower() for h in hints if h.strip()}
    rows = (
        await actor.session.execute(
            select(TaskLabel.id, TaskLabel.name).where(TaskLabel.org_id == actor.org.id)
        )
    ).all()
    return [row[0] for row in rows if row[1].strip().lower() in wanted]


async def _settings_row(session: AsyncSession, org_id: uuid.UUID) -> TaskSettings | None:
    return await session.scalar(select(TaskSettings).where(TaskSettings.org_id == org_id))


# --------------------------------------------------------------------------- #
# The handler
# --------------------------------------------------------------------------- #


def _sender_display(message: IntakeMessage) -> str:
    return message.sender_name or message.sender_email


async def handle_intake_message(ctx: EmitContext, message: IntakeMessage) -> IntakeOutcome:
    """One mail to the task address → a task, a parked row, or a refusal. See the module doc."""
    session = ctx.session
    if message.sender_user_id is None:
        logger.info("task intake: sender %s is not a member; ignored", message.sender_email)
        return IntakeOutcome(status="refused", reason="unknown_sender")

    # 2. The receipt. Inserted inside its own savepoint so the unique index — not application
    #    code — decides whether this copy is the first; the loser answers with the winner's links.
    row = TaskIntakeMessage(
        org_id=ctx.org.id,
        rfc822_message_id=message.rfc822_message_id,
        source=message.source,
        provider_message_id=message.provider_message_id[:512],
        provider_thread_id=(message.provider_thread_id or None),
        sender_user_id=message.sender_user_id,
        sender_email=message.sender_email[:320],
        sender_name=(message.sender_name or None),
        subject=(message.subject or "")[:500] or None,
        body_text=message.body_text,
        body_markdown=message.body_markdown,
        received_at=message.occurred_at,
        status="received",
        hints={},
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await _existing_receipt(session, ctx.org.id, message)
        if existing is None:
            return IntakeOutcome(status="duplicate")
        return IntakeOutcome(
            status="duplicate",
            entity_type=ENTITY_TYPE,
            entity_id=existing.id,
            links=_links_for(existing),
        )
    await _count_receipt(session, ctx.org.id, message.occurred_at)

    # 3. The sender is the actor.
    actor = await member_context(session, ctx.org, message.sender_user_id)
    if actor is None or actor.is_portal or not actor.can("tasks.task.create"):
        await _settle(row, TaskIntakeStatus.REFUSED, reason=REASON_NO_PERMISSION)
        await _store_attachments(ctx, message, ENTITY_TYPE, row.id, row)
        await _notify_parked(ctx, row)
        return IntakeOutcome(
            status="refused", entity_type=ENTITY_TYPE, entity_id=row.id, reason=REASON_NO_PERMISSION
        )

    # 4. The words, then the records, then the model.
    today = await org_today(session, ctx.org.id)
    settings_row = await _settings_row(session, ctx.org.id)
    default_days = settings_row.intake_default_due_days if settings_row else 1
    draft = parse_intake(message.subject, message.body_markdown or message.body_text)
    resolved = await _resolve(actor, message, draft, today=today)
    plan = await _model_plan(actor, message, draft, resolved, today=today)
    if plan is not None:
        _fill_blanks(resolved, plan, draft)
        if resolved.company_id is not None and resolved.company_name is None:
            resolved.company_name = await _company_name(session, ctx.org.id, resolved.company_id)
    row.hints = _hints(draft, resolved, plan)

    if resolved.company_id is None:
        # 5. Parked: the sender finishes it, with everything the mail carried.
        await _settle(
            row, TaskIntakeStatus.NEEDS_CLIENT, reason=resolved.reason or REASON_NO_CLIENT
        )
        await _store_attachments(ctx, message, ENTITY_TYPE, row.id, row)
        await _notify_parked(ctx, row)
        return IntakeOutcome(
            status="parked", entity_type=ENTITY_TYPE, entity_id=row.id, reason=row.reason
        )

    due = resolved.due_date or today + timedelta(days=default_days)
    assignee = resolved.assignee_user_id or message.sender_user_id
    task = await create_task_system(
        ctx,
        title=await _title(ctx, draft, plan),
        company_id=resolved.company_id,
        project_id=resolved.project_id,
        assignee_user_id=assignee,
        description=_description(draft, plan),
        priority=_priority(draft, plan),
        due_date=due,
        actor_name=_sender_display(message),
        actor_user_id=message.sender_user_id,
        requires_interaction=bool(plan and plan.requires_interaction),
        created_payload={"via": "email", "intake_id": str(row.id)},
        # The sender hears about *their* mail through the intake event below, not as a
        # colleague assigning them something; everyone else on the roster hears as usual.
        extra_payload={"_exclude": [message.sender_user_id]},
    )
    await _apply_plan_extras(ctx, task.id, plan, resolved, today=today, intake_id=row.id)
    inline = await _store_attachments(ctx, message, "task", task.id, None)
    if inline and task.description:
        from app.core.htmlmd import rewrite_cid_images

        task.description = rewrite_cid_images(task.description, inline)
    await _settle(row, TaskIntakeStatus.CREATED, task_id=task.id)
    await session.flush()
    await _notify_created(ctx, row, task, resolved, due)
    return IntakeOutcome(
        status="created",
        entity_type="task",
        entity_id=task.id,
        links={"task_id": task.id, "company_id": task.company_id},
    )


async def _existing_receipt(
    session: AsyncSession, org_id: uuid.UUID, message: IntakeMessage
) -> TaskIntakeMessage | None:
    stmt = select(TaskIntakeMessage).where(TaskIntakeMessage.org_id == org_id)
    if message.rfc822_message_id:
        stmt = stmt.where(TaskIntakeMessage.rfc822_message_id == message.rfc822_message_id)
    else:
        stmt = stmt.where(
            TaskIntakeMessage.source == message.source,
            TaskIntakeMessage.provider_message_id == message.provider_message_id[:512],
        )
    return await session.scalar(stmt.limit(1))


def _links_for(row: TaskIntakeMessage) -> dict[str, Any]:
    return {"task_id": row.task_id} if row.task_id else {}


async def _count_receipt(session: AsyncSession, org_id: uuid.UUID, at: datetime) -> None:
    await session.execute(
        update(TaskSettings)
        .where(TaskSettings.org_id == org_id)
        .values(
            intake_last_received_at=at,
            intake_received_count=TaskSettings.intake_received_count + 1,
        )
    )


async def _settle(
    row: TaskIntakeMessage,
    status: TaskIntakeStatus,
    *,
    reason: str | None = None,
    task_id: uuid.UUID | None = None,
) -> None:
    row.status = status.value
    row.reason = reason
    row.task_id = task_id
    row.decided_at = datetime.now(UTC)


async def _resolve(
    actor: RequestContext, message: IntakeMessage, draft: IntakeDraft, *, today: date
) -> Resolved:
    resolved = Resolved()
    if draft.client_hint:
        resolved.company_id, hit = await _company_by_name(actor, draft.client_hint)
        if resolved.company_id is not None:
            resolved.company_name = hit
        else:
            resolved.reason = hit
    if resolved.company_id is None:
        # The forwarded block's people first; failing that, the mail's own recipients — a
        # client thread with ``taak@`` in Cc names its client on the headers, not in the body.
        addresses = list(draft.addresses) or [
            (p.get("email") or "").lower()
            for p in message.participants
            if p.get("email") and p.get("role") in {"from", "to", "cc"}
        ]
        company_id, reason = await _company_by_addresses(actor, message, addresses)
        if company_id is not None:
            resolved.company_id = company_id
            resolved.company_name = await _company_name(actor.session, actor.org.id, company_id)
            resolved.reason = None
        elif reason and resolved.reason is None:
            resolved.reason = reason
    if draft.assignee_hint:
        resolved.assignee_user_id = await _member_by_hint(
            actor, draft.assignee_hint, sender_id=actor.user.id
        )
    if draft.due_hint:
        resolved.due_date = parse_due(draft.due_hint, today=today)
    if draft.project_hint:
        resolved.project_id = await _project_by_hint(actor, draft.project_hint, resolved.company_id)
    resolved.label_ids = await _labels_by_hints(actor, draft.label_hints)
    return resolved


async def _model_plan(
    actor: RequestContext,
    message: IntakeMessage,
    draft: IntakeDraft,
    resolved: Resolved,
    *,
    today: date,
) -> IntakePlan | None:
    if not await intake_ai.available(actor):
        return None
    if not (draft.own_text or draft.forwarded_text or draft.title):
        return None
    document = {
        "subject": message.subject or "",
        "written_by": {"name": message.sender_name, "email": message.sender_email},
        "instruction": draft.own_text[: intake_ai.MAX_BODY_CHARS],
        "forwarded": (draft.forwarded_text[: intake_ai.MAX_BODY_CHARS] or None),
        "attachments": [a.filename for a in message.attachments if not a.content_id][:20],
        "already_decided": {
            "client": resolved.company_name,
            "assignee": bool(resolved.assignee_user_id),
            "deadline": resolved.due_date.isoformat() if resolved.due_date else None,
            "project": bool(resolved.project_id),
        },
    }
    search_text = " ".join(
        part for part in (draft.title, draft.own_text[:2000], draft.client_hint or "") if part
    )
    return await intake_ai.plan_intake(
        actor, document=document, search_text=search_text, body=draft.body, today=today
    )


def _fill_blanks(resolved: Resolved, plan: IntakePlan, draft: IntakeDraft) -> None:
    """The model fills what the sender's own words left blank — never what they decided."""
    if resolved.company_id is None and plan.company_id is not None:
        resolved.company_id = plan.company_id
        resolved.company_name = None
        resolved.reason = None
        resolved.by_model.append("company")
    if resolved.assignee_user_id is None and not draft.assignee_hint and plan.assignee_user_id:
        resolved.assignee_user_id = plan.assignee_user_id
        resolved.by_model.append("assignee")
    if resolved.due_date is None and not draft.due_hint and plan.due_date is not None:
        resolved.due_date = plan.due_date
        resolved.by_model.append("due_date")
    if resolved.project_id is None and not draft.project_hint and plan.project_id is not None:
        resolved.project_id = plan.project_id
        resolved.by_model.append("project")
    if not resolved.label_ids and plan.label_ids:
        resolved.label_ids = list(plan.label_ids)


def _hints(draft: IntakeDraft, resolved: Resolved, plan: IntakePlan | None) -> dict[str, Any]:
    return {
        "title": draft.title,
        "client_hint": draft.client_hint,
        "assignee_hint": draft.assignee_hint,
        "due_hint": draft.due_hint,
        "company_id": str(resolved.company_id) if resolved.company_id else None,
        "assignee_user_id": str(resolved.assignee_user_id) if resolved.assignee_user_id else None,
        "project_id": str(resolved.project_id) if resolved.project_id else None,
        "due_date": resolved.due_date.isoformat() if resolved.due_date else None,
        "label_ids": [str(x) for x in resolved.label_ids],
        "by_model": list(resolved.by_model),
        "plan": _plan_dict(plan),
    }


def _plan_dict(plan: IntakePlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "title": plan.title,
        "summary": plan.summary,
        "priority": plan.priority,
        "checklist_title": plan.checklist_title,
        "checklist_items": [list(item) for item in plan.checklist_items],
        "links": [list(link) for link in plan.links],
        "requires_interaction": plan.requires_interaction,
        "label_ids": [str(x) for x in plan.label_ids],
    }


def _plan_from_dict(data: dict[str, Any] | None) -> IntakePlan | None:
    if not data:
        return None
    plan = IntakePlan(
        title=data.get("title"),
        summary=data.get("summary"),
        priority=data.get("priority"),
        checklist_title=data.get("checklist_title"),
        checklist_items=[(str(i[0]), i[1]) for i in data.get("checklist_items") or [] if i],
        links=[(str(link[0]), link[1]) for link in data.get("links") or [] if link],
        requires_interaction=data.get("requires_interaction"),
    )
    for raw in data.get("label_ids") or []:
        try:
            plan.label_ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return plan


def _priority(draft: IntakeDraft, plan: IntakePlan | None) -> str:
    return draft.priority or (plan.priority if plan else None) or TaskPriority.NORMAL.value


async def _title(ctx: EmitContext, draft: IntakeDraft, plan: IntakePlan | None) -> str:
    """The subject, else the model's phrase, else a translated placeholder in the org's own
    language — a task is named (#391), and a mail with no subject and no words is still a mail
    somebody sent to the task address on purpose."""
    if draft.title:
        return draft.title
    if plan and plan.title:
        return plan.title
    from app.core.models import OrgSettings
    from app.i18n import translate

    locale = await ctx.session.scalar(
        select(OrgSettings.default_locale).where(OrgSettings.org_id == ctx.org.id)
    )
    return translate("tasks.intake.untitled", locale or "nl")[:512]


def _description(draft: IntakeDraft, plan: IntakePlan | None) -> str | None:
    """The mail's words as the task's notes: the model's short summary first, then what the
    colleague wrote, then what they forwarded. Everything through the untrusted strip — half of
    it is an outsider's, and our own mention markup must not survive a forward (#327)."""
    parts: list[str] = []
    if plan and plan.summary:
        parts.append(plan.summary)
    if draft.body:
        parts.append(draft.body)
    joined = "\n\n---\n\n".join(p for p in parts if p)
    return _untrusted_markdown(joined, limit=MAX_DESCRIPTION_CHARS)


async def _apply_plan_extras(
    ctx: EmitContext,
    task_id: uuid.UUID,
    plan: IntakePlan | None,
    resolved: Resolved,
    *,
    today: date,
    intake_id: uuid.UUID,
) -> None:
    """Steps, links and labels onto the fresh task, through the system seam (#327's writer)."""
    applied: dict[str, Any] = {}
    if plan is not None and (plan.checklist_items or plan.links):
        applied = await apply_ai_enrichment_system(
            ctx,
            task_id,
            TaskEnrichment(
                checklist_title=plan.checklist_title,
                checklist_items=plan.checklist_items or None,
                links=plan.links or None,
            ),
            today=today,
        )
    if resolved.label_ids:
        await set_task_labels_system(ctx, task_id, resolved.label_ids)
    if applied or resolved.by_model:
        await record_ai_activity_system(
            ctx,
            task_id,
            "ai_intake",
            {"intake_id": str(intake_id), "by_model": list(resolved.by_model), **applied},
        )


async def _store_attachments(
    ctx: EmitContext,
    message: IntakeMessage,
    entity_type: str,
    entity_id: uuid.UUID,
    row: TaskIntakeMessage | None,
) -> dict[str, str]:
    """Every part with bytes onto the host; inline parts keep their ``content_id`` so the body's
    ``cid:`` markers resolve. Returns ``{content id: file id}`` and, for a parked row, rewrites
    its own body in place."""
    if not message.attachments:
        return {}
    if await storage_system.entity_has_files(ctx, entity_type, entity_id):
        return {}
    resolved: dict[str, str] = {}
    for part in message.attachments:
        stored = await storage_system.store_system_file(
            ctx,
            filename=part.filename,
            content_type=part.content_type,
            data=part.data,
            entity_type=entity_type,
            entity_id=entity_id,
            content_id=part.content_id,
            created_by_user_id=message.sender_user_id,
        )
        if stored is None:
            logger.info("task intake: attachment skipped (type/size): %s", part.filename)
        elif part.content_id:
            resolved[part.content_id] = str(stored.id)
    if resolved and row is not None and row.body_markdown:
        from app.core.htmlmd import rewrite_cid_images

        row.body_markdown = rewrite_cid_images(row.body_markdown, resolved)
    return resolved


async def _notify_created(
    ctx: EmitContext, row: TaskIntakeMessage, task: Task, resolved: Resolved, due: date
) -> None:
    assignee_name = None
    if task.assignee_user_id and task.assignee_user_id != row.sender_user_id:
        assignee_name = await ctx.session.scalar(
            text("SELECT COALESCE(full_name, email) FROM users WHERE id = :uid"),
            {"uid": task.assignee_user_id},
        )
    await emit(
        CREATED_EVENT,
        ctx,
        {
            "task_id": task.id,
            "title": task.title,
            "subject": row.subject or "",
            "company": resolved.company_name or "",
            "assignee": assignee_name or "",
            "due_date": due.isoformat(),
            "by_model": ", ".join(resolved.by_model),
            "_recipients": [row.sender_user_id],
            "_dedup_key": f"task-intake-created:{row.id}",
        },
    )


async def _notify_parked(ctx: EmitContext, row: TaskIntakeMessage) -> None:
    await emit(
        PARKED_EVENT,
        ctx,
        {
            "task_intake_id": row.id,
            "subject": row.subject or "",
            "reason": row.reason or "",
            "_recipients": [row.sender_user_id],
            "_dedup_key": f"task-intake-parked:{row.id}",
        },
    )


# --------------------------------------------------------------------------- #
# The request side — settings, the parked queue, finishing by hand
# --------------------------------------------------------------------------- #


class TaskIntakeService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # settings ---------------------------------------------------------------
    async def settings(self) -> TaskSettingsRead:
        row = await _settings_row(self.ctx.session, self.ctx.org.id)
        if row is None:
            return TaskSettingsRead()
        return TaskSettingsRead(
            intake_address=row.intake_address,
            intake_default_due_days=row.intake_default_due_days,
            intake_last_received_at=row.intake_last_received_at,
            intake_received_count=row.intake_received_count,
        )

    async def update_settings(self, data: TaskSettingsUpdate) -> TaskSettingsRead:
        """Absent means leave alone; an explicit ``null`` address switches the intake off."""
        self.ctx.require("tasks.settings.manage")
        sent = data.model_dump(exclude_unset=True)
        values = {k: v for k, v in sent.items() if k != "intake_address" and v is not None}
        if "intake_address" in sent:
            values["intake_address"] = sent["intake_address"]
        repo = self.ctx.repo(TaskSettings)
        row = await _settings_row(self.ctx.session, self.ctx.org.id)
        if row is None:
            await repo.create(**values)
        elif values:
            await repo.update(row, **values)
        return await self.settings()

    # the queue --------------------------------------------------------------
    def _mine(self):  # noqa: ANN202
        return (
            self.ctx.repo(TaskIntakeMessage)
            .scoped_select()
            .where(TaskIntakeMessage.sender_user_id == self.ctx.user.id)
        )

    async def list_mine(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[TaskIntakeRead]:
        self.ctx.require("tasks.task.create")
        stmt = self._mine().order_by(TaskIntakeMessage.received_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(TaskIntakeMessage.status == status)
        rows = (await self.ctx.session.execute(stmt)).scalars().all()
        return [TaskIntakeRead.model_validate(row) for row in rows]

    async def summary(self) -> TaskIntakeSummary:
        self.ctx.require("tasks.task.create")
        count = await self.ctx.session.scalar(
            select(func.count())
            .select_from(TaskIntakeMessage)
            .where(
                TaskIntakeMessage.org_id == self.ctx.org.id,
                TaskIntakeMessage.sender_user_id == self.ctx.user.id,
                TaskIntakeMessage.status == TaskIntakeStatus.NEEDS_CLIENT.value,
            )
        )
        return TaskIntakeSummary(needs_client=int(count or 0))

    async def get_mine(self, intake_id: uuid.UUID) -> TaskIntakeMessage:
        self.ctx.require("tasks.task.create")
        row = await self.ctx.session.scalar(self._mine().where(TaskIntakeMessage.id == intake_id))
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def complete(self, intake_id: uuid.UUID, data: TaskIntakeComplete) -> Task:
        """Finish a parked mail as the person: an ordinary ``TaskService.create`` under every
        rule a form submit meets, then the mail's attachments move onto the task."""
        from app.modules.tasks.service import TaskService

        row = await self.get_mine(intake_id)
        if row.status not in {TaskIntakeStatus.NEEDS_CLIENT.value, TaskIntakeStatus.REFUSED.value}:
            raise AppError("conflict", "errors.tasks_intake_already_decided", status_code=409)
        hints = row.hints or {}
        plan = _plan_from_dict(hints.get("plan"))
        draft = parse_intake(row.subject, row.body_markdown or row.body_text)
        today = await org_today(self.ctx.session, self.ctx.org.id)
        settings_row = await _settings_row(self.ctx.session, self.ctx.org.id)
        default_days = settings_row.intake_default_due_days if settings_row else 1
        due = data.due_date or _iso(hints.get("due_date")) or today + timedelta(days=default_days)
        company_id = data.company_id or _uuid(hints.get("company_id"))
        if company_id is None and data.project_id is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"company_id": "errors.tasks_company_required"},
            )
        body: dict[str, Any] = {
            "title": data.title or hints.get("title") or await _title(self.ctx, draft, plan),
            "company_id": company_id,
            "project_id": data.project_id or _uuid(hints.get("project_id")),
            "due_date": due,
            "description": _description(draft, plan),
            "priority": _priority(draft, plan),
            "requires_interaction": bool(plan and plan.requires_interaction),
            "label_ids": [uuid.UUID(x) for x in hints.get("label_ids") or []],
        }
        if data.assignees is not None:
            body["assignees"] = [a.model_dump() for a in data.assignees]
        elif data.assignee_user_id is not None:
            body["assignee_user_id"] = data.assignee_user_id
        elif hints.get("assignee_user_id"):
            body["assignee_user_id"] = _uuid(hints.get("assignee_user_id"))
        if plan and plan.checklist_items:
            body["checklist"] = {
                "title": plan.checklist_title,
                "items": [{"title": t, "description": d} for t, d in plan.checklist_items],
            }
        if plan and plan.links:
            body["links"] = [{"url": u, "title": t} for u, t in plan.links]
        task = await TaskService(self.ctx).create(TaskCreate(**body))
        await self._rehome_files(row.id, task.id)
        await _settle(row, TaskIntakeStatus.CREATED, task_id=task.id)
        await self.ctx.session.flush()
        return task

    async def _rehome_files(self, intake_id: uuid.UUID, task_id: uuid.UUID) -> None:
        await self.ctx.session.execute(
            update(StoredFile)
            .where(
                StoredFile.org_id == self.ctx.org.id,
                StoredFile.entity_type == ENTITY_TYPE,
                StoredFile.entity_id == intake_id,
            )
            .values(entity_type="task", entity_id=task_id)
        )

    async def discard(self, intake_id: uuid.UUID) -> None:
        row = await self.get_mine(intake_id)
        if row.status == TaskIntakeStatus.CREATED.value:
            raise AppError("conflict", "errors.tasks_intake_already_decided", status_code=409)
        await _settle(row, TaskIntakeStatus.DISCARDED)
        await self.ctx.session.flush()


def _uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _iso(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = [
    "CREATED_EVENT",
    "ENTITY_TYPE",
    "INTAKE_KEY",
    "PARKED_EVENT",
    "IntakeDraft",
    "TaskIntakeService",
    "clean_subject",
    "handle_intake_message",
    "intake_addresses_for_org",
    "parse_due",
    "parse_intake",
    "split_forward",
]
