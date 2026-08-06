"""Interaction service — feed, manual CRUD, and the gmail review flow (approve/reject/remap).

The review rule, agreed with the user: **only the mailbox owner** decides about their own
gmail-sourced rows. Unlike every other own/any permission, ``interactions.interaction.review``
deliberately has no ``:any`` escalation — an admin must not be able to approve a colleague's
email into the CRM, because the thing being protected is the *mailbox owner's* judgment, not a
capability tier. Manual rows follow the ordinary own/any write/delete scopes.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.sql.expression import column as sa_column
from sqlalchemy.sql.expression import table as sa_table

from app.config import settings
from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.auth.models import User
from app.core.directory import ids_by_email, visible_ids
from app.core.events import emit
from app.core.htmlmd import rewrite_cid_images
from app.core.models import Membership
from app.core.richtext import (
    extract_contact_mention_ids,
    extract_mention_ids,
    markdown_excerpt,
    sanitize_markdown,
)
from app.core.sorting import apply_sort, user_sort_name
from app.core.storage.service import FileService
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.interactions.eml import (
    SNIPPET_CHARS,
    EmlAttachment,
    EmlParseError,
    looks_like_eml,
    parse_eml,
)
from app.modules.interactions.models import (
    DEFAULT_KINDS,
    ENTITY_TYPE,
    HOST_ENTITY,
    PROTECTED_KIND,
    Interaction,
    InteractionContact,
    InteractionDirection,
    InteractionKindDef,
    InteractionSource,
    InteractionStatus,
)
from app.modules.interactions.schemas import (
    InteractionApprove,
    InteractionBulkApprove,
    InteractionBulkAssign,
    InteractionBulkLinks,
    InteractionBulkReject,
    InteractionCreate,
    InteractionKindDefCreate,
    InteractionKindDefUpdate,
    InteractionRemap,
    InteractionUpdate,
)
from app.modules.interactions.system import (
    resolve_conversation_id,
    resolve_upload_conversation_id,
)

logger = logging.getLogger("schakl.interactions")

#: Fields whose edits land in the activity trail (§16) — the record's own definition, not body.
#: The contact roster is audited too, as a ``contact_ids`` list each write path merges into the
#: before/after snapshots: it is not a column, so ``snapshot()`` cannot reach it, and leaving it
#: out would make "we added the technical contact" an untracked edit.
_AUDITED_FIELDS = (
    "kind",
    "occurred_at",
    "subject",
    "direction",
    "company_id",
    "project_id",
    "task_id",
    "contact_id",
)

_LINK_TABLES = {
    "company_id": "companies",
    "project_id": "projects",
    "task_id": "tasks",
    "contact_id": "contacts",
}

#: Must match ``notifications.events.INTERACTION_MENTIONED`` (#151) — a string on the bus,
#: like the gmail feed's ``PENDING_EVENT``, never a cross-module import (CLAUDE.md §6).
MENTIONED_EVENT = "interactions.mentioned"

#: How much of an untitled row stands in for its subject where one line quotes it — a trail
#: entry, a bell notification. Shorter than the timeline's ``SNIPPET_CHARS``: those read on a
#: row of their own, these are quoted mid-sentence after somebody's name.
LINE_TEASER_CHARS = 80

# A lightweight table ref, like ``_LINK_TABLES``' raw SQL — never the contacts module's
# internals (CLAUDE.md §6). Only what the sort expression touches.
_contacts = sa_table(
    "contacts",
    sa_column("id"),
    sa_column("org_id"),
    sa_column("first_name"),
    sa_column("last_name"),
)


def _contact_display_name() -> Any:
    """A contact's display name, built from the bare table ref above — the label a chip prints."""
    return func.trim(
        func.concat(_contacts.c.first_name, " ", func.coalesce(_contacts.c.last_name, ""))
    )


def _contact_sort_name() -> Any:
    """Order by the contact's display name — the label the chip prints (docs/UX.md: a column
    sorts by what it prints, never by the FK). Correlated, so a row is never multiplied.

    The **lead** contact's name, deliberately: a roster (#300) has no single name to sort by,
    and folding several into one sort key would order the list by a string no column shows.
    The lead is what the collapsed cell prints, so the column still sorts by what it prints.
    """
    return (
        select(func.lower(_contact_display_name()))
        .where(_contacts.c.org_id == Interaction.org_id, _contacts.c.id == Interaction.contact_id)
        .scalar_subquery()
    )


def _kind_sort_position() -> Any:
    """Kinds are a tenant-defined vocabulary (#174), so they sort by *meaning*: the tenant's
    declared ``position`` — the order the kind dropdown shows — not alphabetically, which the
    per-locale JSONB labels could not express server-side anyway (docs/UX.md)."""
    return (
        select(InteractionKindDef.position)
        .where(
            InteractionKindDef.org_id == Interaction.org_id,
            InteractionKindDef.key == Interaction.kind,
        )
        .scalar_subquery()
    )


# Columns a client may sort by; anything else in ``?sort=`` is rejected (app/core/sorting.py).
# The owner falls back to the snapshotted name so a departed user's rows still file by name (#64).
SORTABLE = {
    "occurred_at": Interaction.occurred_at,
    "subject": func.lower(Interaction.subject),
    "kind": _kind_sort_position(),
    "contact": _contact_sort_name(),
    "owner": func.coalesce(
        user_sort_name(Interaction.owner_user_id), func.lower(Interaction.owner_name)
    ),
}


class InteractionService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Interaction)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    def _horizon(self, stmt):
        """AND the caller's company data horizon (#191) onto a hand-built statement.

        This module's reads are window folds, thread fetches and ``count(DISTINCT …)``
        expressions, none of which can be built from ``scoped_select()`` — which is exactly how
        the overview ended up with no horizon at all (#240). Everything reading or counting
        ``Interaction`` rows goes through here instead. A no-op for an unrestricted membership,
        which is every owner and everyone in no company group.
        """
        condition = self.repo.horizon_condition()
        return stmt if condition is None else stmt.where(condition)

    # --- reads ---------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        contact_id: uuid.UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        owner_user_id: uuid.UUID | None = None,
        include: str | None = None,
        q: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        sort: str | None = None,
        count: bool = True,
        with_body: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """A page of folded conversations, and (unless opted out) how many there are.

        Two opt-outs, because this list is the heaviest one in the app (docs/PERFORMANCE.md):

        ``count=False`` skips the ``count(DISTINCT …)`` second pass over the same filter — the
        fold makes it a genuinely separate aggregate, not a free by-product. Pass it wherever
        the total is not rendered.

        ``with_body`` is **off** by default: a list row carries only what the list draws, and
        ``body_text`` is a full e-mail body per row. Twenty of them is the bulk of the response
        for a column that shows ``snippet``. The key stays in the payload as ``None`` so the
        response shape never changes; the detail view fetches the row it opens.
        """
        include_set = {part.strip() for part in (include or "").split(",") if part.strip()}
        conditions = []
        # A pending row is private to its mailbox owner until approved — with NO admin or
        # read_all escape (owner feedback on #172: "rule 1, always private per account").
        # An unreviewed email is the mailbox owner's mail, not the org's record yet; it only
        # joins the team timeline when its owner approves it.
        conditions.append(
            or_(
                Interaction.status != InteractionStatus.PENDING.value,
                Interaction.owner_user_id == self.ctx.user.id,
            )
        )
        # The company data horizon (#191, #240) — as a plain condition rather than via
        # ``_horizon``, because the fold subquery and the total each build their own statement
        # from ``conditions``. That is the point: the rows the fold considers, the
        # representative it picks and the total all describe the same horizon, so the page and
        # its count can't disagree (#252's rule). An explicit ``company_id`` outside the
        # horizon ANDs to nothing and returns an empty page, never another client's timeline.
        horizon = self.repo.horizon_condition()
        if horizon is not None:
            conditions.append(horizon)
        if not self.ctx.can("interactions.interaction.read_all"):
            # Someone else's queue is not a filter you may use (#168).
            if owner_user_id is not None and owner_user_id != self.ctx.user.id:
                raise AppError("forbidden", "errors.forbidden", status_code=403)
        if company_id is not None:
            # The client timeline is already complete without a roll-up: a task/project link
            # derives ``company_id`` on write (``_resolve_links``), so filtering the FK is it.
            conditions.append(Interaction.company_id == company_id)
        if project_id is not None:
            if "tasks" in include_set:
                # A project's communication is its own plus its tasks' (#147): one OR over two
                # indexed FKs, the task ids fetched once — never a per-row lookup.
                task_ids = (
                    await self.ctx.session.scalars(
                        text("SELECT id FROM tasks WHERE org_id = :oid AND project_id = :pid"),
                        {"oid": self._org_id, "pid": project_id},
                    )
                ).all()
                own = Interaction.project_id == project_id
                conditions.append(or_(own, Interaction.task_id.in_(task_ids)) if task_ids else own)
            else:
                conditions.append(Interaction.project_id == project_id)
        if task_id is not None:
            conditions.append(Interaction.task_id == task_id)
        if contact_id is not None:
            # Anyone on the roster, not only the lead (#300): a meeting this person attended
            # second is still their meeting, and their panel would otherwise omit it. An
            # ``EXISTS`` over ``(org_id, contact_id)`` — one index probe, whatever the roster
            # size — rather than a join, which would multiply the folded feed's rows.
            conditions.append(
                select(InteractionContact.id)
                .where(
                    InteractionContact.org_id == Interaction.org_id,
                    InteractionContact.interaction_id == Interaction.id,
                    InteractionContact.contact_id == contact_id,
                )
                .exists()
            )
        if kind:
            conditions.append(Interaction.kind == kind)
        if status:
            conditions.append(Interaction.status == status)
        if owner_user_id is not None:
            conditions.append(Interaction.owner_user_id == owner_user_id)
        if q:
            like = f"%{q}%"
            conditions.append(
                or_(
                    Interaction.subject.ilike(like),
                    Interaction.snippet.ilike(like),
                    Interaction.body_text.ilike(like),
                )
            )
        if date_from is not None or date_to is not None:
            # The bounds are local calendar days (#238): "March" means March on the tenant's
            # clock — the same zone the day-group headers use — never a UTC `.date()`, which
            # would drag the previous evening's rows into the window half the year (docs/UX.md).
            tz = await org_zoneinfo(self.ctx.session, self._org_id)
            if date_from is not None:
                conditions.append(
                    Interaction.occurred_at >= datetime.combine(date_from, time.min, tzinfo=tz)
                )
            if date_to is not None:
                upper = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
                conditions.append(Interaction.occurred_at < upper)
        # Gmail-style folding (#272): a conversation collapses to one representative row — the
        # newest message — before pagination and the total, so paging/totals describe what's
        # shown, not raw rows. The group key is COALESCE(conversation_id, id): a NULL row (every
        # manual/pending row, by construction) is its own singleton, so this is a no-op for them.
        group_key = func.coalesce(Interaction.conversation_id, Interaction.id)
        folded = (
            select(
                Interaction.id.label("iid"),
                func.row_number()
                .over(
                    partition_by=group_key,
                    order_by=(Interaction.occurred_at.desc(), Interaction.id.desc()),
                )
                .label("rn"),
            )
            .where(Interaction.org_id == self._org_id, *conditions)
            .subquery()
        )
        stmt = (
            select(Interaction, User.full_name, User.email)
            .join(folded, folded.c.iid == Interaction.id)
            .outerjoin(User, User.id == Interaction.owner_user_id)
            .where(folded.c.rn == 1)
        )
        # Newest-first stays the default (#238); an explicit sort tiebreaks on the timeline so
        # pagination is deterministic when the sorted values repeat. Sorting acts on the
        # representative rows (the fold already picked the newest per conversation).
        stmt = apply_sort(stmt, sort, SORTABLE, default=Interaction.occurred_at.desc())
        if sort:
            stmt = stmt.order_by(Interaction.occurred_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.ctx.session.execute(stmt)).all()
        # The total is the number of distinct conversations matching the filter, not raw rows,
        # so pagination lines up with the folded list. Skipped when the caller says it never
        # renders one — this is a second full pass over the filter, not a cheap by-product.
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count(func.distinct(group_key)))
                    .select_from(Interaction)
                    .where(Interaction.org_id == self._org_id, *conditions)
                )
                or 0
            )
            if count
            else len(rows)
        )
        plain_rows = [row for row, _, _ in rows]
        names = await self._link_names(plain_rows)
        rosters = await self._contact_rosters(plain_rows)
        contacts_by_email = await self._participant_contacts(plain_rows)
        members_by_email = await self._participant_members(plain_rows)
        closing_ids = await self._closing_task_ids(plain_rows)
        # The badge counts the **whole** conversation, not just the rows matching this filter
        # (#272): an entity panel filtered to one company/project/task still shows the true
        # message count and opens the full thread, even when only the representative is linked
        # here. Batched over the page's conversation ids — never per row (docs/PERFORMANCE.md) —
        # and consistent with what _present_one/thread() already report for a single row.
        conv_counts = await self._conversation_counts(plain_rows)
        return [
            self._present(
                row,
                full_name,
                email,
                names,
                contacts_by_email,
                members_by_email,
                closing_ids,
                conversation_count=conv_counts.get(row.conversation_id, 1),
                with_body=with_body,
                rosters=rosters,
            )
            for row, full_name, email in rows
        ], total

    async def _conversation_counts(
        self, rows: list[Interaction]
    ) -> dict[uuid.UUID, int]:
        """How many logged messages each conversation on this page holds (#272) — one batched,
        org-scoped count over the page's distinct conversation ids, so the fold badge reflects
        the whole thread rather than only the rows that matched the current filter."""
        conv_ids = {row.conversation_id for row in rows if row.conversation_id is not None}
        if not conv_ids:
            return {}
        stmt = self._horizon(
            select(Interaction.conversation_id, func.count()).where(
                Interaction.org_id == self._org_id,
                Interaction.conversation_id.in_(conv_ids),
                Interaction.status == InteractionStatus.LOGGED.value,
            )
        ).group_by(Interaction.conversation_id)
        return {cid: int(n) for cid, n in (await self.ctx.session.execute(stmt)).all()}

    async def get(self, interaction_id: uuid.UUID) -> dict[str, Any]:
        row = await self.repo.get_or_404(interaction_id)
        # A pending row is its owner's alone until approved — absent, not forbidden, so the
        # id leaks nothing (§15). No read_all/admin escape (owner feedback on #172).
        if (
            row.status == InteractionStatus.PENDING.value
            and row.owner_user_id != self.ctx.user.id
        ):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return await self._present_one(row)

    async def thread(self, interaction_id: uuid.UUID) -> list[dict[str, Any]]:
        """The full conversation an interaction belongs to (#272), newest first — what the
        detail modal expands into. A row not in a conversation is its own one-message thread.

        No owner filter: a logged row is team-visible regardless of who owns it, exactly like
        the plain list. The anchor still runs the pending-privacy check via ``get()``, and the
        thread carries the company horizon like the feed does (#240) — a conversation can be
        remapped message by message, so reaching one visible row must not hand a restricted
        membership the messages filed under a client it cannot see.
        """
        anchor = await self.get(interaction_id)
        conversation_id = anchor["conversation_id"]
        if conversation_id is None:
            return [anchor]
        stmt = (
            select(Interaction, User.full_name, User.email)
            .outerjoin(User, User.id == Interaction.owner_user_id)
            .where(
                Interaction.org_id == self._org_id,
                Interaction.conversation_id == conversation_id,
                Interaction.status == InteractionStatus.LOGGED.value,
            )
            .order_by(Interaction.occurred_at.desc(), Interaction.id.desc())
        )
        stmt = self._horizon(stmt)
        rows = (await self.ctx.session.execute(stmt)).all()
        plain_rows = [row for row, _, _ in rows]
        names = await self._link_names(plain_rows)
        rosters = await self._contact_rosters(plain_rows)
        contacts_by_email = await self._participant_contacts(plain_rows)
        members_by_email = await self._participant_members(plain_rows)
        closing_ids = await self._closing_task_ids(plain_rows)
        count = len(plain_rows)
        return [
            self._present(
                row,
                full_name,
                email,
                names,
                contacts_by_email,
                members_by_email,
                closing_ids,
                conversation_count=count,
                rosters=rosters,
            )
            for row, full_name, email in rows
        ]

    # --- manual writes ---------------------------------------------------------- #
    async def create(self, data: InteractionCreate) -> dict[str, Any]:
        self.ctx.require("interactions.interaction.write")
        await self._require_manual_kind(data.kind)
        # The roster is resolved *before* the row exists, so an unseeable contact fails the
        # write instead of leaving a logged moment behind with half a roster on it.
        roster = await self._requested_roster(data.model_dump(exclude_unset=True)) or []
        links = await self._resolve_links(
            {
                "company_id": data.company_id,
                "project_id": data.project_id,
                "task_id": data.task_id,
                # The lead is chip 0, never the field the caller happened to send (schemas.py).
                "contact_id": roster[0] if roster else None,
            }
        )
        user = self.ctx.user
        body = sanitize_markdown(data.body_text)
        mentioned = await self._valid_mentions(extract_mention_ids(body))
        mentioned_contacts = await self._valid_contact_mentions(extract_contact_mention_ids(body))
        row = await self.repo.create(
            kind=data.kind,
            status=InteractionStatus.LOGGED.value,
            occurred_at=await self._as_instant(data.occurred_at),
            # Blank is ``NULL``, never ``""`` — the column is nullable and every reader falls
            # back to the kind's label, which an empty string would defeat.
            subject=(data.subject or "").strip() or None,
            body_text=body,
            direction=data.direction.value,
            owner_user_id=user.id,
            owner_name=user.full_name or user.email,
            participants=[p.model_dump() for p in data.participants],
            mentioned_user_ids=[str(uid) for uid in mentioned],
            mentioned_contact_ids=[str(cid) for cid in mentioned_contacts],
            source=InteractionSource.MANUAL.value,
            **links,
        )
        await self._set_contacts(row, roster)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id)
        await self._record_on_hosts(row, "interaction.logged", contact_ids=roster)
        await self._notify_mentions(row, mentioned)
        if data.log_time is not None:
            await self._log_time(row, data.log_time)
        return await self._present_one(row)

    async def _log_time(self, row: Interaction, log_time: Any) -> None:
        """The "Voeg aan mijn uren toe" ride-along (#175): a linked time entry, in this same
        transaction, through the time module's published surface (§6) — never its internals.
        Carries the interaction's own links and subject, and its kind as the entry's *type* —
        the time module mirrors the kind into a matching type on first use (#182), so a logged
        call/meeting is typed even though time-entry types seed only work/email."""
        self.ctx.require("time.entry.write")
        from app.modules.time import system as time_system

        kind = await self.ctx.session.scalar(
            select(InteractionKindDef).where(
                InteractionKindDef.org_id == self._org_id, InteractionKindDef.key == row.kind
            )
        )
        entry_type_key = await time_system.ensure_type_for_kind(
            self.ctx, row.kind, kind.label_i18n if kind else None
        )
        await time_system.record_entry(
            self.ctx,
            user_id=self.ctx.user.id,
            started_at=log_time.started_at,
            ended_at=log_time.ended_at,
            company_id=row.company_id,
            project_id=row.project_id,
            task_id=row.task_id,
            # A subject is optional (schemas.py), so the notes stand in for it: a timesheet row
            # reading "Gebeld over de verlenging…" beats a blank one on a call nobody titled.
            description=row.subject or markdown_excerpt(row.body_text, SNIPPET_CHARS),
            entry_type_key=entry_type_key,
            interaction_id=row.id,
        )

    # --- uploaded email (#262) --------------------------------------------------- #
    async def create_from_eml(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        links: dict[str, Any],
        allow_duplicate: bool = False,
    ) -> tuple[dict[str, Any], int, int]:
        """Log an uploaded ``.eml`` as a ``kind="email"`` interaction (#262).

        Deliberately **not** a loosening of ``create()``: ``email`` stays the one kind a person
        may never type into the ordinary form (#174), because a hand-written "email" is a note
        pretending to be a message. This path may write one because it isn't hand-written — the
        fields come out of a real RFC 5322 message, in the same shape the gmail feed produces.

        It lands ``logged``, not ``pending``: the review step exists to catch mail the poller
        ingested *for* you, which cannot apply to a file someone deliberately picked.
        """
        self.ctx.require("interactions.interaction.write")
        if not looks_like_eml(filename, content_type):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"file": "errors.interactions_eml_type"},
            )
        if not data:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"file": "errors.required"},
            )
        if len(data) > settings.upload_max_bytes:
            raise AppError(
                "validation",
                "errors.upload_too_large",
                status_code=413,
                fields={"file": "errors.upload_too_large"},
            )
        try:
            parsed = parse_eml(data)
        except EmlParseError:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"file": "errors.interactions_eml_invalid"},
            ) from None
        # Cross-source dedup on the global Message-ID, the same key the gmail feed dedups on.
        # A warning, not a wall (#262): the same message may legitimately need logging from a
        # mailbox nobody connected — so the caller confirms and re-sends with allow_duplicate.
        if parsed.rfc822_message_id and not allow_duplicate:
            duplicate = await self.ctx.session.scalar(
                select(Interaction.id).where(
                    Interaction.org_id == self._org_id,
                    Interaction.rfc822_message_id == parsed.rfc822_message_id,
                )
            )
            if duplicate is not None:
                raise AppError(
                    "conflict", "errors.interactions_eml_duplicate", status_code=409
                )
        roster = await self._requested_roster(links) or []
        resolved = await self._resolve_links(
            {
                field: links.get(field)
                for field in ("company_id", "project_id", "task_id")
            }
            | {"contact_id": roster[0] if roster else None}
        )
        user = self.ctx.user
        # Conversation grouping by RFC 5322 headers (#272): the thread root is the oldest id in
        # the References/In-Reply-To chain, or the message's own id when it starts a thread. The
        # upload folds onto any logged email it threads with (a gmail-synced original it replies
        # to, or another upload of the same thread) — see resolve_upload_conversation_id.
        thread_root_id = (
            parsed.reference_ids[0] if parsed.reference_ids else parsed.rfc822_message_id
        ) or None
        conversation_id = await resolve_upload_conversation_id(
            self.ctx,
            rfc822_message_id=parsed.rfc822_message_id,
            reference_ids=parsed.reference_ids,
            thread_root_id=thread_root_id,
        )
        row = await self.repo.create(
            kind=PROTECTED_KIND,
            status=InteractionStatus.LOGGED.value,
            # No usable Date header: the upload itself is the only honest timestamp, and the
            # uploader can correct it — inventing one from the filename would be worse.
            occurred_at=parsed.occurred_at or datetime.now(UTC),
            subject=(parsed.subject or "")[:500] or None,
            snippet=parsed.snippet,
            # Stored raw, like a gmail body: it is a received message, never markdown of ours.
            body_text=parsed.body_text,
            # …and the formatted half, which *is* markdown of ours — we converted it from the
            # message's own HTML part, which is exactly what makes rendering it as markdown
            # honest. Its `cid:` image markers are rewritten below, once the parts they point
            # at are stored files with ids.
            body_markdown=parsed.body_markdown,
            direction=await self._upload_direction(parsed.from_email),
            owner_user_id=user.id,
            owner_name=user.full_name or user.email,
            participants=parsed.participants,
            source=InteractionSource.UPLOAD.value,
            rfc822_message_id=(parsed.rfc822_message_id or None),
            conversation_id=conversation_id,
            thread_root_id=thread_root_id,
            **resolved,
        )
        await self._set_contacts(row, roster)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id, {"source": "eml"})
        await self._record_on_hosts(row, "interaction.logged", contact_ids=roster)
        stored, skipped, inline = await self._store_eml_attachments(row.id, parsed.attachments)
        if inline:
            # The body pointed at parts that are now files. Rewriting here rather than at
            # render time keeps the stored body self-contained, and an unstored cid degrades
            # to its alt text instead of leaving a marker nothing resolves.
            row = await self.repo.update(
                row, body_markdown=rewrite_cid_images(row.body_markdown, inline)
            )
        return await self._present_one(row), stored, skipped

    async def _upload_direction(self, from_email: str | None) -> str:
        """Inbound unless the sender is one of us — the closest an uploaded file can get to the
        ``SENT`` label the gmail feed reads, using the same membership match the participant
        chips already do (#167)."""
        if not from_email:
            return InteractionDirection.NONE.value
        mine = await self.ctx.session.scalar(
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == self._org_id, func.lower(User.email) == from_email)
        )
        return (
            InteractionDirection.OUTBOUND if mine is not None else InteractionDirection.INBOUND
        ).value

    async def _store_eml_attachments(
        self, interaction_id: uuid.UUID, attachments: list[EmlAttachment]
    ) -> tuple[int, int, dict[str, str]]:
        """Store the message's parts through the ordinary, permission-checked file service
        (#123) — this is a person's own upload, not the gmail worker's system write.

        Returns ``(stored, skipped, {content id: file id})``. The counts are about
        **attachments**, which an inline part is not: a signature logo is content of the body,
        so reporting "3 bijlagen" for one PDF and two logos would describe a message nobody
        received. An inline part that the guardrails refuse is not a skipped attachment either
        — the body simply falls back to its alt text.

        One rejected attachment must never lose the message (the gmail path's rule, #180), so
        anything the storage guardrails would refuse is skipped and **counted**: the response
        says how many, rather than quietly dropping a client's PDF.
        """
        if not attachments:
            return 0, 0, {}
        if not self.ctx.can("files.file.write"):
            return 0, sum(1 for a in attachments if not a.content_id), {}
        files = FileService(self.ctx)
        stored = skipped = 0
        inline: dict[str, str] = {}
        for attachment in attachments:
            if (
                attachment.content_type not in settings.upload_allowed_types
                or len(attachment.data) > settings.upload_max_bytes
            ):
                logger.info(
                    "eml attachment skipped (type/size) for %s: %s",
                    interaction_id,
                    attachment.filename,
                )
                skipped += 0 if attachment.content_id else 1
                continue
            row = await files.create(
                filename=attachment.filename,
                content_type=attachment.content_type,
                stream=io.BytesIO(attachment.data),
                size_bytes=len(attachment.data),
                entity_type=ENTITY_TYPE,
                entity_id=interaction_id,
                content_id=attachment.content_id,
            )
            if attachment.content_id:
                inline[attachment.content_id] = str(row.id)
            else:
                stored += 1
        return stored, skipped, inline

    async def update(self, interaction_id: uuid.UUID, data: InteractionUpdate) -> dict[str, Any]:
        row = await self._writable_or_404(interaction_id, "interactions.interaction.write")
        self._reviewless_only(row)
        old_contacts = await self._contact_ids_of(row.id)
        before = snapshot(row, _AUDITED_FIELDS) | {"contact_ids": [str(c) for c in old_contacts]}
        sent = data.model_dump(exclude_unset=True)
        # Keeping the row's own kind is always allowed — a deactivated kind must not brick
        # editing the rows that already carry it (#174).
        if sent.get("kind") is not None and sent["kind"] != row.kind:
            # An email is an email: the protected kind is no more re-typeable *away* than it is
            # settable by hand (#262), or an uploaded message would launder itself into a note.
            if row.kind == PROTECTED_KIND:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"kind": "errors.interactions_kind_not_manual"},
                )
            await self._require_manual_kind(sent["kind"])
        # The contact link is applied as a roster below, not as a column here (#300).
        link_updates = {k: sent[k] for k in _LINK_TABLES if k in sent and k != "contact_id"}
        values: dict[str, Any] = {
            k: v
            for k, v in sent.items()
            if k not in _LINK_TABLES and k not in ("participants", "contact_ids")
        }
        if values.get("direction") is not None:
            values["direction"] = values["direction"].value
        if "subject" in values:
            # Sent-and-blank clears it (schemas.py); an absent key never reaches here at all.
            values["subject"] = (values["subject"] or "").strip() or None
        if "participants" in sent:
            values["participants"] = [p.model_dump() for p in data.participants or []]
        if values.get("occurred_at") is not None:
            values["occurred_at"] = await self._as_instant(values["occurred_at"])
        # An edited body re-extracts its mentions (#151); only people mentioned for the first
        # time are notified — re-saving a note must not re-ping everyone already in it.
        newly_mentioned: list[uuid.UUID] = []
        if "body_text" in values:
            already = set(row.mentioned_user_ids or [])
            body = sanitize_markdown(values["body_text"])
            values["body_text"] = body
            # An edited body is the author's, not the sender's: the converted formatting
            # described the message as received and would now contradict the text on screen.
            values["body_markdown"] = None
            mentioned = await self._valid_mentions(extract_mention_ids(body))
            values["mentioned_user_ids"] = [str(uid) for uid in mentioned]
            values["mentioned_contact_ids"] = [
                str(cid)
                for cid in await self._valid_contact_mentions(extract_contact_mention_ids(body))
            ]
            newly_mentioned = [uid for uid in mentioned if str(uid) not in already]
        values.update(await self._resolve_links(link_updates, partial=True))
        old_links = {field: getattr(row, field) for field in HOST_ENTITY}
        row = await self.repo.update(row, **values)
        # Before the "after" snapshot, so a roster change is a diff on the trail and not a
        # silent edit — and before the host bookkeeping, which reads the roster it left.
        await self._apply_roster(row, await self._requested_roster(sent), old=old_contacts)
        after = snapshot(row, _AUDITED_FIELDS) | {
            "contact_ids": [str(c) for c in await self._contact_ids_of(row.id)]
        }
        await ActivityService(self.ctx).record_update(ENTITY_TYPE, row.id, before, after)
        await self._record_link_moves(row, old_links)
        await self._notify_mentions(row, newly_mentioned)
        return await self._present_one(row)

    async def delete(self, interaction_id: uuid.UUID) -> None:
        row = await self._writable_or_404(interaction_id, "interactions.interaction.delete")
        self._reviewless_only(row)
        await self.repo.delete(row)

    # --- gmail review flow (owner-only, no :any escape) ------------------------- #
    async def approve(
        self, interaction_id: uuid.UUID, data: InteractionApprove | None = None
    ) -> dict[str, Any]:
        row = await self._owned_gmail_or_404(interaction_id)
        self._pending_only(row)
        link_values: dict[str, Any] = {}
        roster: list[uuid.UUID] | None = None
        if data is not None:
            sent = data.model_dump(exclude_unset=True)
            if sent:
                link_values = await self._resolve_links(
                    {k: v for k, v in sent.items() if k in _LINK_TABLES and k != "contact_id"},
                    partial=True,
                )
                roster = await self._requested_roster(sent)
        row = await self._approve_row(row, link_values, roster)
        return await self._present_one(row)

    async def _approve_row(
        self, row: Interaction, link_values: dict[str, Any], roster: list[uuid.UUID] | None
    ) -> Interaction:
        """Approve one already-loaded, already-eligible row.

        The single endpoint and the bulk one both land here, so a batch of fifty does exactly
        what fifty clicks do — same trail, same host mirrors, same conversation folding, same
        bus emit. A second implementation of this is the one way bulk review could quietly
        stop meaning what review means.

        ``roster`` is the batch's contact roster **already resolved** (#300), or ``None`` for
        "leave each row's own alone" — the same shape ``link_values`` has, and for the same
        reason: a bad id is the payload being wrong, so it is validated once for the call
        rather than per row.
        """
        # Optionally assign links in the same step as approval (#183) — no need to approve
        # then reopen and move. Applied before the row goes team-visible, so the "moved"
        # bookkeeping (unlink from an old host nobody saw) doesn't apply; the host announce
        # below fires on the *final* links.
        before = snapshot(row, _AUDITED_FIELDS) | {
            "contact_ids": [str(c) for c in await self._contact_ids_of(row.id)]
        }
        # A pending row becoming logged is the other moment it can join a conversation (#272):
        # inherit the newest logged sibling's id in this gmail thread, minting one if that
        # sibling has none yet, so the two fold together the instant this one lands.
        conversation_id = await resolve_conversation_id(
            self.ctx, row.gmail_thread_id, exclude_id=row.id
        )
        row = await self.repo.update(
            row,
            status=InteractionStatus.LOGGED.value,
            conversation_id=conversation_id,
            **link_values,
        )
        # No ``old=`` here on purpose: the row was pending, so nobody had been told it existed
        # yet — the host announce below fires on the *final* roster, once, like every link.
        await self._apply_roster(row, roster)
        final_roster = await self._contact_ids_of(row.id)
        await ActivityService(self.ctx).record(ENTITY_TYPE, row.id, "approved")
        if link_values or roster is not None:
            after = snapshot(row, _AUDITED_FIELDS) | {
                "contact_ids": [str(c) for c in final_roster]
            }
            await ActivityService(self.ctx).record_update(ENTITY_TYPE, row.id, before, after)
        # Approval is the moment the email becomes team-visible — that is when the host
        # records hear about it (#152); a pending row must not announce itself.
        await self._record_on_hosts(row, "interaction.logged", contact_ids=final_roster)
        # The google module fetches the body asynchronously — never inside this transaction.
        await emit(
            "interaction.approved",
            self.ctx,
            {
                "interaction_id": row.id,
                "owner_user_id": row.owner_user_id,
                "gmail_message_id": row.gmail_message_id,
            },
        )
        return row

    async def reject(self, interaction_id: uuid.UUID, *, suppress_thread: bool = False) -> None:
        """The owner keeps this email out of the CRM: metadata removed, message suppressed."""
        row = await self._owned_gmail_or_404(interaction_id)
        await self._reject_row(row, suppress_thread=suppress_thread)

    async def _reject_row(self, row: Interaction, *, suppress_thread: bool) -> None:
        """Reject one already-loaded, already-eligible row — the shared path (see
        ``_approve_row``). The bus emit is what writes the Gmail suppression, in this same
        transaction, so the rejection and its "never again" still commit together in a batch."""
        await emit(
            "interaction.rejected",
            self.ctx,
            {
                "interaction_id": row.id,
                "owner_user_id": row.owner_user_id,
                "gmail_message_id": row.gmail_message_id,
                "gmail_thread_id": row.gmail_thread_id,
                "suppress_thread": suppress_thread,
            },
        )
        await self.repo.delete(row)

    async def remap(self, interaction_id: uuid.UUID, data: InteractionRemap) -> dict[str, Any]:
        row = await self._owned_gmail_or_404(interaction_id)
        sent = data.model_dump(exclude_unset=True)
        if not sent:
            return await self._present_one(row)
        values = await self._resolve_links(
            {k: v for k, v in sent.items() if k in _LINK_TABLES and k != "contact_id"},
            partial=True,
        )
        roster = await self._requested_roster(sent)
        return await self._present_one(await self._remap_row(row, values, roster))

    async def _remap_row(
        self, row: Interaction, values: dict[str, Any], roster: list[uuid.UUID] | None
    ) -> Interaction:
        """Re-file one already-loaded, already-eligible row — the shared path (see
        ``_approve_row``, whose ``roster`` contract this shares)."""
        old_contacts = await self._contact_ids_of(row.id)
        before = snapshot(row, _AUDITED_FIELDS) | {"contact_ids": [str(c) for c in old_contacts]}
        old_links = {field: getattr(row, field) for field in HOST_ENTITY}
        row = await self.repo.update(row, **values)
        await self._apply_roster(row, roster, old=old_contacts)
        after = snapshot(row, _AUDITED_FIELDS) | {
            "contact_ids": [str(c) for c in await self._contact_ids_of(row.id)]
        }
        await ActivityService(self.ctx).record_update(ENTITY_TYPE, row.id, before, after)
        await self._record_link_moves(row, old_links)
        # Remapping is the owner engaging with the row — enough to retire the "waiting on
        # your review" notification about it (#170). Bus-only, like approve/reject.
        await emit(
            "interaction.remapped",
            self.ctx,
            {"interaction_id": row.id, "owner_user_id": row.owner_user_id},
        )
        return row

    # --- bulk review (#299) ----------------------------------------------------- #
    # A queue of forty auto-matched emails is reviewed a screenful at a time or not at all, so
    # the review flow gets a batch form. Three things make that safe rather than reckless, and
    # they are worth stating because the obvious worry — "bulk approve skips the step where I
    # connect the email to a client/project/task" — turns out to be the wrong one:
    #
    # 1. **Approving does not decide the links.** ``InteractionApprove`` already treats an
    #    absent field as "leave this row's own link alone", so a bulk approve that sends none
    #    is a pure status change: every row keeps exactly what the gmail matcher derived for
    #    it. It never blanket-overwrites forty rows with one client.
    # 2. **Approving does not close the connect step either.** ``remap`` has no status check —
    #    a *logged* gmail row is still re-filable by its owner, forever. "Approve now, file
    #    later" is a real workflow, which is what makes approving in bulk a cheap decision.
    #    ``bulk_assign`` is the other order: file the batch first, read and approve after.
    # 3. **The irreversible one is reject**, not approve: it deletes the row *and* suppresses
    #    the message so a re-poll never brings it back. So the batch that needs the loud
    #    confirmation is bulk deny — the inverse of where the caution instinctively goes.
    async def bulk_approve(self, data: InteractionBulkApprove) -> dict[str, Any]:
        link_values = await self._bulk_links(data)
        roster = await self._bulk_roster(data)
        rows, failed = await self._bulk_eligible(data.ids, pending_only=True)
        for row in rows:
            await self._approve_row(row, dict(link_values), roster)
        return {"succeeded": len(rows), "failed": failed}

    async def bulk_assign(self, data: InteractionBulkAssign) -> dict[str, Any]:
        """File a selection without approving it. Unlike approve, a *logged* row is fair game —
        this is the batch form of ``remap``, so it also re-files a mis-matched run of emails."""
        link_values = await self._bulk_links(data)
        roster = await self._bulk_roster(data)
        rows, failed = await self._bulk_eligible(data.ids, pending_only=False)
        # A batch that names a roster and no links is still a real assignment (#300), so
        # "nothing to do" has to ask about both — testing links alone would silently drop
        # "put these six emails on Jan and Piet".
        if not link_values and roster is None:
            return {"succeeded": 0, "failed": failed}
        for row in rows:
            await self._remap_row(row, dict(link_values), roster)
        return {"succeeded": len(rows), "failed": failed}

    async def bulk_reject(self, data: InteractionBulkReject) -> dict[str, Any]:
        """Permanent, per row: the metadata goes and the message is suppressed, so a re-poll
        never resurrects it. The ``interaction.rejected`` subscriber writes each suppression in
        this transaction — one small indexed lookup per row, which is why ``MAX_BULK_IDS``
        bounds the batch rather than leaving it open-ended."""
        rows, failed = await self._bulk_eligible(data.ids, pending_only=True)
        for row in rows:
            await self._reject_row(row, suppress_thread=data.suppress_thread)
        return {"succeeded": len(rows), "failed": failed}

    async def _bulk_links(self, data: InteractionBulkLinks) -> dict[str, Any]:
        """Resolve the batch's shared links **once**, before any row is touched.

        A ``company_id`` that does not exist is the caller's payload being wrong, not any one
        row's problem — every row would fail on it identically. So this raises 422 for the
        whole call, while row-level trouble is reported instead (``InteractionBulkResult``).
        """
        sent = data.model_dump(
            exclude_unset=True, exclude={"ids", "contact_id", "contact_ids"}
        )
        return await self._resolve_links(sent, partial=True) if sent else {}

    async def _bulk_roster(self, data: InteractionBulkLinks) -> list[uuid.UUID] | None:
        """The batch's shared roster, resolved once (see ``_bulk_links``) — ``None`` for
        "leave every row's own alone", which is what an absent field means here (#300)."""
        return await self._requested_roster(
            data.model_dump(exclude_unset=True, exclude={"ids"})
        )

    async def _bulk_eligible(
        self, ids: list[uuid.UUID], *, pending_only: bool
    ) -> tuple[list[Interaction], list[dict[str, Any]]]:
        """Load the selection in one query and split it into "can" and "cannot, because".

        One ``IN`` rather than a ``get_or_404`` per id (docs/PERFORMANCE.md): the batch is the
        whole point, and a per-row load would make the cheap half of the work the expensive
        half. The read rides ``scoped_select()``, so tenant isolation and the company horizon
        both come along — a bulk call can no more name a row across the horizon than a list can.
        """
        unique = list(dict.fromkeys(ids))  # a double-checked row is approved once, not twice
        found = {
            row.id: row
            for row in (
                await self.ctx.session.execute(
                    self.repo.scoped_select().where(Interaction.id.in_(unique))
                )
            )
            .scalars()
            .all()
        }
        rows: list[Interaction] = []
        failed: list[dict[str, Any]] = []
        for interaction_id in unique:
            row = found.get(interaction_id)
            if row is None:
                # Outside the tenant, outside the horizon, or already gone — one answer for
                # all three (§15): an id you cannot act on must not read as an id that exists.
                failed.append({"id": interaction_id, "error": "errors.not_found"})
                continue
            reason = self._review_ineligible(row)
            if reason is None and pending_only and row.status != InteractionStatus.PENDING.value:
                reason = ("invalid_state", "errors.interactions_not_pending", 409)
            if reason is not None:
                failed.append({"id": interaction_id, "error": reason[1]})
                continue
            rows.append(row)
        return rows, failed

    async def add_to_conversation(
        self, interaction_id: uuid.UUID, target_interaction_id: uuid.UUID
    ) -> dict[str, Any]:
        """Manually glue this gmail email onto another's conversation (#272) — for a reply Gmail
        didn't thread automatically (a different-address sender, a forwarded copy).

        Gated like every other gmail-row mutation here (module docstring): only the mailbox owner
        decides about their own gmail-sourced rows, no ``:any`` escape. The target is scoped to
        the owner's own logged gmail rows too, which sidesteps mutating a colleague's row.
        """
        row = await self._owned_gmail_or_404(interaction_id)
        if row.status != InteractionStatus.LOGGED.value:
            raise AppError("invalid_state", "errors.interactions_not_logged", status_code=409)
        target = await self.repo.get_or_404(target_interaction_id)  # tenant-scoped
        if (
            target.id == row.id
            or target.source != InteractionSource.GMAIL.value
            or target.status != InteractionStatus.LOGGED.value
            or target.owner_user_id != self.ctx.user.id
        ):
            # 422 for an ineligible *body-supplied* reference (like ``_ensure_exists``); the URL
            # path id still 404s via ``_owned_gmail_or_404``.
            raise AppError(
                "validation",
                "errors.interactions_conversation_invalid_target",
                status_code=422,
                fields={"target_interaction_id": "errors.interactions_conversation_invalid_target"},
            )
        if target.conversation_id is None:
            target.conversation_id = uuid.uuid4()
        row.conversation_id = target.conversation_id
        await self.ctx.session.flush()
        # No bus emit — nothing reacts to this today. It is the owner engaging with the row, so
        # it lands on the interaction's own trail like a remap.
        await ActivityService(self.ctx).record(
            ENTITY_TYPE,
            row.id,
            "interaction.conversation_linked",
            {"target_interaction_id": str(target.id)},
        )
        return await self._present_one(row)

    # --- the contact roster (#300) ----------------------------------------------- #
    @staticmethod
    def _roster_intent(sent: dict[str, Any]) -> list[uuid.UUID] | None:
        """What this payload asks the roster to become, or ``None`` for "leave it alone".

        The one place the two-field contract in ``schemas.py`` is interpreted: ``contact_ids``
        wins where it was sent, a bare ``contact_id`` still means a one-person roster (which is
        what keeps every pre-#300 caller writing exactly what it wrote before), and neither
        touching the payload leaves the stored roster untouched. ``sent`` is an
        ``exclude_unset`` dump, so "absent" and "explicitly null" are genuinely distinguishable
        here — on a partial write that distinction is the whole difference between *keep* and
        *clear*.
        """
        if sent.get("contact_ids") is not None:
            return list(sent["contact_ids"])
        if "contact_id" in sent:
            return [sent["contact_id"]] if sent["contact_id"] is not None else []
        return None

    async def _resolve_contact_ids(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Validate a requested roster: deduplicated, order kept, nobody invisible.

        Through the reference seam (``core/directory.py``) rather than the bare
        ``SELECT … WHERE org_id`` the other three links use, for the reason §15 spells out: a
        contact's client lives in ``company_contacts`` and in no column this module may know
        about, so "belongs to this org" would let a company-group-scoped member file a moment
        against a person at a client they cannot see — and every screen afterwards renders that
        as perfectly ordinary.
        """
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        visible = await visible_ids(self.ctx, "contact", wanted)
        if any(cid not in visible for cid in wanted):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"contact_ids": "errors.not_found"},
            )
        return wanted

    async def _contact_ids_of(self, interaction_id: uuid.UUID) -> list[uuid.UUID]:
        """The stored roster, in chip order."""
        stmt = (
            select(InteractionContact.contact_id)
            .where(
                InteractionContact.org_id == self._org_id,
                InteractionContact.interaction_id == interaction_id,
            )
            .order_by(InteractionContact.position, InteractionContact.created_at)
        )
        return list((await self.ctx.session.execute(stmt)).scalars())

    async def _set_contacts(self, row: Interaction, ids: list[uuid.UUID]) -> None:
        """Make the roster exactly ``ids`` and re-stamp the lead column, in this transaction.

        Reconciled rather than delete-and-reinsert: an unchanged chip keeps its link row (and
        its ``created_at``), so "since when has this person been on this moment" survives an
        edit that only added someone else. ``row.contact_id`` is rewritten on every call — the
        column is a mirror of chip 0 and the two must never be written apart (models.py).
        """
        links = self.ctx.repo(InteractionContact)
        existing = list(
            (
                await self.ctx.session.execute(
                    links.scoped_select().where(InteractionContact.interaction_id == row.id)
                )
            )
            .scalars()
            .all()
        )
        by_contact = {link.contact_id: link for link in existing}
        for position, contact_id in enumerate(ids):
            link = by_contact.pop(contact_id, None)
            if link is None:
                await links.create(
                    interaction_id=row.id, contact_id=contact_id, position=position
                )
            elif link.position != position:
                link.position = position
        for orphan in by_contact.values():
            await links.delete(orphan)
        row.contact_id = ids[0] if ids else None
        await self.ctx.session.flush()

    async def _requested_roster(self, sent: dict[str, Any]) -> list[uuid.UUID] | None:
        """This payload's roster, validated — or ``None`` for "leave the stored one alone".

        Split from applying it so a **batch resolves once** (``_bulk_roster``): a contact the
        caller cannot see is the payload being wrong, which every row in the selection would
        fail on identically, so it is the call that gets the 422 rather than fifty rows.
        """
        requested = self._roster_intent(sent)
        return None if requested is None else await self._resolve_contact_ids(requested)

    async def _apply_roster(
        self,
        row: Interaction,
        resolved: list[uuid.UUID] | None,
        *,
        old: list[uuid.UUID] | None = None,
    ) -> None:
        """Write an already-resolved roster onto a stored row, and tell the people it moved.

        ``None`` leaves the stored roster untouched. ``old`` is the roster as it stood before
        the write (the caller reads it once, before touching anything); a create passes none
        because there is nothing to have left.
        """
        if resolved is None:
            return
        await self._set_contacts(row, resolved)
        if old is not None:
            await self._record_contact_moves(row, old, resolved)

    async def _contact_rosters(
        self, rows: list[Interaction]
    ) -> dict[uuid.UUID, list[dict[str, Any]]]:
        """Every row's roster on this page, labelled, in **one** query (docs/PERFORMANCE.md).

        Joined to ``contacts`` rather than resolved per row: this is the read that would
        otherwise be one lookup per chip per row, which is invisible at three rows and is the
        whole response at three hundred.

        A page where nothing names anybody costs **no** query at all, which is what keeps the
        company detail page on the budget it had before rosters existed: a non-empty roster
        always mirrors chip 0 onto ``contact_id`` (models.py), so an all-``NULL`` page provably
        has no links to fetch. That invariant is the same one the lead column exists for.
        """
        ids = [row.id for row in rows if row.contact_id is not None]
        if not ids:
            return {}
        stmt = (
            select(
                InteractionContact.interaction_id,
                InteractionContact.contact_id,
                _contact_display_name(),
            )
            .join(
                _contacts,
                (_contacts.c.id == InteractionContact.contact_id)
                & (_contacts.c.org_id == InteractionContact.org_id),
            )
            .where(
                InteractionContact.org_id == self._org_id,
                InteractionContact.interaction_id.in_(ids),
            )
            .order_by(
                InteractionContact.interaction_id,
                InteractionContact.position,
                InteractionContact.created_at,
            )
        )
        rosters: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for interaction_id, contact_id, name in (await self.ctx.session.execute(stmt)).all():
            rosters.setdefault(interaction_id, []).append({"id": contact_id, "name": name})
        return rosters

    # --- helpers ---------------------------------------------------------------- #
    def _host_payload(self, row: Interaction) -> dict[str, Any]:
        # The host's trail quotes the subject (`activity.action.interaction.logged`), so an
        # untitled row falls back to the source's snippet, then to its own opening words —
        # anything but a pair of empty quotes on the company's activity feed.
        return {
            "interaction_id": str(row.id),
            "kind": row.kind,
            "subject": row.subject
            or row.snippet
            or markdown_excerpt(row.body_text, LINE_TEASER_CHARS),
        }

    async def _record_on_hosts(
        self, row: Interaction, action: str, *, contact_ids: list[uuid.UUID] | None = None
    ) -> None:
        """Mirror a milestone onto every linked host record's trail (#152), in the same
        transaction. A mirror *event* carrying a pointer — the field-level diff stays on the
        interaction's own trail, so nothing is audited twice.

        **Every** person on the roster hears about it (#300), not only the lead: a call with
        two people is on both their records, or the second one's page quietly omits the moment
        they were in. ``contact_ids`` is passed by callers that just wrote the roster and
        already hold it; anyone else pays one indexed read.
        """
        activity = ActivityService(self.ctx)
        payload = self._host_payload(row)
        for field, entity_type in HOST_ENTITY.items():
            if field == "contact_id":
                continue  # the roster below is the authority, and the column is only its lead
            target_id = getattr(row, field)
            if target_id is not None:
                await activity.record(entity_type, target_id, action, payload)
        roster = contact_ids if contact_ids is not None else await self._contact_ids_of(row.id)
        for contact_id in roster:
            await activity.record(HOST_ENTITY["contact_id"], contact_id, action, payload)

    async def _record_link_moves(
        self, row: Interaction, old_links: dict[str, uuid.UUID | None]
    ) -> None:
        """A moved contactmoment tells both sides (#152): the host it left and the one it
        joined. Only team-visible (logged) rows announce themselves.

        The contact link is not read here: it is a roster now, and its own moves are recorded
        by :meth:`_record_contact_moves` from the before/after lists the write already has.
        """
        if row.status != InteractionStatus.LOGGED.value:
            return
        activity = ActivityService(self.ctx)
        payload = self._host_payload(row)
        for field, entity_type in HOST_ENTITY.items():
            if field == "contact_id":
                continue
            old, new = old_links[field], getattr(row, field)
            if old == new:
                continue
            if old is not None:
                await activity.record(entity_type, old, "interaction.unlinked", payload)
            if new is not None:
                await activity.record(entity_type, new, "interaction.linked", payload)

    async def _record_contact_moves(
        self, row: Interaction, old: list[uuid.UUID], new: list[uuid.UUID]
    ) -> None:
        """The roster's half of the same bookkeeping (#300): whoever left the moment and
        whoever joined it hears about it on their own trail. Reordering is not a move."""
        if row.status != InteractionStatus.LOGGED.value:
            return
        activity = ActivityService(self.ctx)
        payload = self._host_payload(row)
        entity_type = HOST_ENTITY["contact_id"]
        before, after = set(old), set(new)
        for contact_id in old:
            if contact_id not in after:
                await activity.record(entity_type, contact_id, "interaction.unlinked", payload)
        for contact_id in new:
            if contact_id not in before:
                await activity.record(entity_type, contact_id, "interaction.linked", payload)

    async def _valid_mentions(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Keep only the mentioned ids that are members of this org (#151, like #63)."""
        if not ids:
            return []
        members = set(
            (
                await self.ctx.session.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == self._org_id, Membership.user_id.in_(ids)
                    )
                )
            ).scalars()
        )
        return [uid for uid in ids if uid in members]

    async def _valid_contact_mentions(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Keep only the mentioned contact ids **this caller can see** (#165) — a reference
        into the CRM, never a notification: contacts have no inbox here.

        Through the reference seam for the same reason as ``_participant_contacts``: "belongs
        to this org" was the whole test, so a company-group-scoped member could paste any
        contact id in the tenant into a note and have it render as that person's name.
        """
        if not ids:
            return []
        found = await visible_ids(self.ctx, "contact", ids)
        return [cid for cid in ids if cid in found]

    async def _notify_mentions(self, row: Interaction, mentioned: list[uuid.UUID]) -> None:
        """Tell the people newly @mentioned in this note — never the author themselves."""
        recipients = [uid for uid in mentioned if uid != self.ctx.user.id]
        if not recipients:
            return
        await emit(
            MENTIONED_EVENT,
            self.ctx,
            {
                "interaction_id": row.id,
                # The bell line quotes this (`notifications.event.interactions.mentioned`), so
                # an untitled note falls back to its own opening words rather than to “”.
                "subject": row.subject or markdown_excerpt(row.body_text, LINE_TEASER_CHARS),
                # Link targets for the notification (format.ts): the host the note hangs on.
                "task_id": row.task_id,
                "project_id": row.project_id,
                "company_id": row.company_id,
                "contact_id": row.contact_id,
                "_recipients": recipients,
            },
        )

    async def _as_instant(self, value: datetime) -> datetime:
        """A naive datetime is the org's wall clock (§8): attach the tenant zone, store an instant.

        Gmail-fed rows arrive as true UTC instants; a hand-typed "14:00" must mean 14:00 on the
        tenant's clock, or the two sources drift two hours apart on one timeline.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=await org_zoneinfo(self.ctx.session, self.ctx.org.id))
        return value

    async def _require_manual_kind(self, kind: str) -> None:
        """A manual row's kind must be one of the org's active kinds — and never ``email``,
        which only the gmail feed writes (#174)."""
        if kind != PROTECTED_KIND:
            active = await InteractionKindService(self.ctx).active_keys()
            if kind in active:
                return
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"kind": "errors.interactions_kind_not_manual"},
        )

    def _reviewless_only(self, row: Interaction) -> None:
        """Gmail rows change through the review flow, never through plain edit/delete.

        An uploaded ``.eml`` (#262) has no review flow — nobody's mailbox is behind it — so it
        edits and deletes like any row its owner logged by hand.
        """
        if row.source == InteractionSource.GMAIL.value:
            raise AppError("invalid_state", "errors.interactions_gmail_readonly", status_code=409)

    async def _writable_or_404(self, interaction_id: uuid.UUID, permission: str) -> Interaction:
        """Own/any scoped load: someone else's row without ``:any`` reads as absent (§15)."""
        row = await self.repo.get_or_404(interaction_id)
        if row.owner_user_id == self.ctx.user.id:
            self.ctx.require(permission, scope="own")
            return row
        if not self.ctx.can(permission, scope="any"):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def _owned_gmail_or_404(self, interaction_id: uuid.UUID) -> Interaction:
        """Review actions: gmail-sourced and strictly the caller's own mailbox — no override."""
        row = await self.repo.get_or_404(interaction_id)
        reason = self._review_ineligible(row)
        if reason is not None:
            raise AppError(*reason[:2], status_code=reason[2])
        return row

    def _review_ineligible(self, row: Interaction) -> tuple[str, str, int] | None:
        """Why this row is out of the review flow, or ``None`` when it is in it.

        One statement of the rule, asked two ways: ``_owned_gmail_or_404`` raises it for a
        single row, the bulk loader reports it per row. Splitting them is how a batch would
        end up quietly reviewing a colleague's mailbox.
        """
        if row.source != InteractionSource.GMAIL.value:
            return ("invalid_state", "errors.interactions_manual_no_review", 409)
        if row.owner_user_id != self.ctx.user.id:
            return ("forbidden", "errors.interactions_owner_only", 403)
        return None

    def _pending_only(self, row: Interaction) -> None:
        if row.status != InteractionStatus.PENDING.value:
            raise AppError("invalid_state", "errors.interactions_not_pending", status_code=409)

    async def _resolve_links(
        self, links: dict[str, uuid.UUID | None], *, partial: bool = False
    ) -> dict[str, uuid.UUID | None]:
        """Validate link targets against their bare tables (§6) and derive ``company_id``.

        A task/project link fills a missing company link from the target row, so the client
        timeline stays complete without query-time roll-ups. On partial updates the derivation
        only runs when the caller touched a link but not the company.
        """
        values: dict[str, uuid.UUID | None] = {}
        for field_name, target_id in links.items():
            # ``contact_id`` is never the caller's to place any more (#300): it is chip 0 of a
            # roster ``_resolve_contact_ids`` has already validated through the reference seam,
            # which is *stricter* than this bare-table check — re-asking here would buy a query
            # and no safety.
            if target_id is not None and field_name != "contact_id":
                await self._ensure_exists(_LINK_TABLES[field_name], field_name, target_id)
            values[field_name] = target_id
        derived = await self._derived_company(values)
        if derived is not None and not values.get("company_id"):
            if not partial or "company_id" not in values:
                values["company_id"] = derived
        return values

    async def _derived_company(self, values: dict[str, uuid.UUID | None]) -> uuid.UUID | None:
        for field_name, table in (("task_id", "tasks"), ("project_id", "projects")):
            target_id = values.get(field_name)
            if target_id is None:
                continue
            company_id = await self.ctx.session.scalar(
                text(f"SELECT company_id FROM {table} WHERE id = :tid AND org_id = :oid"),  # noqa: S608 — fixed table names
                {"tid": target_id, "oid": self._org_id},
            )
            if company_id is not None:
                return company_id
        return None

    async def _ensure_exists(self, table: str, field_name: str, target_id: uuid.UUID) -> None:
        exists = await self.ctx.session.scalar(
            text(f"SELECT 1 FROM {table} WHERE id = :tid AND org_id = :oid"),  # noqa: S608 — fixed table names
            {"tid": target_id, "oid": self._org_id},
        )
        if not exists:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={field_name: "errors.not_found"},
            )

    #: What each linked table calls its label — the batched name lookup below reads these.
    #: No ``contact_id`` entry: the roster query (``_contact_rosters``) already labels every
    #: chip, and the lead's name is chip 0's. Asking the contacts table twice per page would be
    #: the same lookup, priced twice (docs/PERFORMANCE.md).
    _LINK_LABELS = {
        "company_id": ("companies", "name"),
        "project_id": ("projects", "name"),
        "task_id": ("tasks", "title"),
    }

    async def _link_names(self, rows: list[Interaction]) -> dict[tuple[str, uuid.UUID], str]:
        """Labels for the linked records (#147) — one batched query per referenced table for
        the whole page, never a per-row lookup (docs/PERFORMANCE.md). Raw ids are worse than
        saying nothing, and the web should not need four lookup fetches to draw a chip."""
        wanted: dict[str, set[uuid.UUID]] = {field: set() for field in self._LINK_LABELS}
        for row in rows:
            for field in self._LINK_LABELS:
                value = getattr(row, field)
                if value is not None:
                    wanted[field].add(value)
        names: dict[tuple[str, uuid.UUID], str] = {}
        for field, ids in wanted.items():
            if not ids:
                continue
            table, label = self._LINK_LABELS[field]
            stmt = text(
                f"SELECT id, {label} FROM {table} WHERE org_id = :oid AND id IN :ids"  # noqa: S608 — fixed table/label names
            ).bindparams(bindparam("ids", expanding=True))
            result = await self.ctx.session.execute(stmt, {"oid": self._org_id, "ids": list(ids)})
            for target_id, target_label in result:
                names[(field, target_id)] = target_label
        return names

    async def _closing_task_ids(self, rows: list[Interaction]) -> set[uuid.UUID]:
        """Which of these interactions is a task's designated closing moment (#157) — one
        batched, org-scoped query over the page's ids, so the web can mark the row that closed
        a task without a per-row lookup (docs/PERFORMANCE.md)."""
        ids = [row.id for row in rows]
        if not ids:
            return set()
        stmt = text(
            "SELECT closing_interaction_id FROM tasks "
            "WHERE org_id = :oid AND closing_interaction_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        result = await self.ctx.session.execute(stmt, {"oid": self._org_id, "ids": ids})
        return {row[0] for row in result if row[0] is not None}

    async def _present_one(self, row: Interaction) -> dict[str, Any]:
        owner = (
            (
                await self.ctx.session.execute(
                    select(User.full_name, User.email).where(User.id == row.owner_user_id)
                )
            ).first()
            if row.owner_user_id
            else None
        )
        names = await self._link_names([row])
        rosters = await self._contact_rosters([row])
        contacts_by_email = await self._participant_contacts([row])
        members_by_email = await self._participant_members([row])
        closing_ids = await self._closing_task_ids([row])
        # A single-row endpoint, not list-scale: one extra indexed count is fine here, only when
        # the row is actually in a conversation (docs/PERFORMANCE.md — the concern is per-row
        # lookups over a *page*, not one lookup for one record).
        conversation_count = 1
        if row.conversation_id is not None:
            conversation_count = int(
                await self.ctx.session.scalar(
                    self._horizon(
                        select(func.count())
                        .select_from(Interaction)
                        .where(
                            Interaction.org_id == self._org_id,
                            Interaction.conversation_id == row.conversation_id,
                            Interaction.status == InteractionStatus.LOGGED.value,
                        )
                    )
                )
                or 1
            )
        return self._present(
            row,
            owner[0] if owner else None,
            owner[1] if owner else None,
            names,
            contacts_by_email,
            members_by_email,
            closing_ids,
            conversation_count=conversation_count,
            rosters=rosters,
        )

    async def _participant_contacts(self, rows: list[Interaction]) -> dict[str, uuid.UUID]:
        """Which participant addresses exist as contacts **this caller may see** (#160).

        One batched query over the page's distinct emails, matched at read time so a contact
        created after the email was logged still links up. Display data, never authz — but it
        goes through the cross-module reference seam (``core/directory.py``) rather than a bare
        ``WHERE org_id`` read, because a contact's client lives in ``company_contacts`` and not
        in a column here: org-scoped alone, this chipped every other client's people onto a
        thread a company-group-scoped member could legitimately open.
        """
        emails: set[str] = set()
        for row in rows:
            for participant in row.participants or []:
                email = (participant.get("email") or "").lower()
                if email:
                    emails.add(email)
        return await ids_by_email(self.ctx, "contact", emails)

    async def _participant_members(self, rows: list[Interaction]) -> dict[str, uuid.UUID]:
        """Which participant addresses belong to org employees (#167) — the same batched,
        read-time pass as ``_participant_contacts``, joined through ``memberships`` so a user
        record from another org never resolves here. Display data, never authz."""
        emails: set[str] = set()
        for row in rows:
            for participant in row.participants or []:
                email = (participant.get("email") or "").lower()
                if email:
                    emails.add(email)
        if not emails:
            return {}
        stmt = (
            select(func.lower(User.email), User.id)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == self._org_id, func.lower(User.email).in_(emails))
        )
        result = await self.ctx.session.execute(stmt)
        return dict(result.all())

    def _present(
        self,
        row: Interaction,
        live_name: str | None,
        live_email: str | None,
        names: dict[tuple[str, uuid.UUID], str] | None = None,
        contacts_by_email: dict[str, uuid.UUID] | None = None,
        members_by_email: dict[str, uuid.UUID] | None = None,
        closing_ids: set[uuid.UUID] | None = None,
        conversation_count: int = 1,
        with_body: bool = True,
        rosters: dict[uuid.UUID, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Owner resolved like the activity trail (issue #64): live account wins, snapshot after.

        ``with_body=False`` blanks ``body_text`` for list rows: the key stays (so the response
        schema is unchanged and a client can tell "not loaded" from "no body" only by asking
        for the row), but a page of full e-mail bodies never crosses the wire to render a
        snippet column. Every single-row path leaves it on.
        """
        if live_email is not None:
            owner_name, owner_deleted = live_name or live_email, False
        else:
            owner_name = row.owner_name
            owner_deleted = row.owner_name is not None
        names = names or {}
        # The roster is the authority; the two singular keys are chip 0 (models.py). Derived
        # here rather than read off the column so a read can never disagree with itself.
        roster = (rosters or {}).get(row.id, [])
        lead = roster[0] if roster else None
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "occurred_at": row.occurred_at,
            "subject": row.subject,
            # A row we wrote has no ``snippet`` column to read: only a *source* hands us one
            # (Gmail's own, the ``.eml`` parser's), so a hand-logged call or note carried its
            # text in ``body_text`` and previewed as nothing at all — the timeline drew a title
            # and a timestamp over an empty space where the words were. The stored column keeps
            # meaning "the preview the source gave us"; the field means "the teaser to show",
            # and for our own markdown that is derived here. Deliberately not written on save:
            # derived, every row that already exists gains its preview without a backfill, and
            # an edited note can never leave a stale teaser behind. Costs nothing extra — the
            # list already selects the whole row, and ``with_body`` only blanks the payload.
            "snippet": row.snippet or markdown_excerpt(row.body_text, SNIPPET_CHARS),
            "body_text": row.body_text if with_body else None,
            # Rides ``with_body`` for the same reason: it is the *larger* of the two, and a
            # page of formatted e-mail bodies to draw a snippet column is exactly the payload
            # #290 took off the list.
            "body_markdown": row.body_markdown if with_body else None,
            "direction": row.direction,
            "company_id": row.company_id,
            "project_id": row.project_id,
            "task_id": row.task_id,
            "contact_id": lead["id"] if lead else None,
            "contacts": roster,
            "company_name": names.get(("company_id", row.company_id)),
            "project_name": names.get(("project_id", row.project_id)),
            "task_title": names.get(("task_id", row.task_id)),
            "contact_name": lead["name"] if lead else None,
            "closes_task": row.id in (closing_ids or set()),
            "owner_user_id": row.owner_user_id,
            "owner_name": owner_name,
            "owner_deleted": owner_deleted,
            "participants": [
                {
                    **participant,
                    "contact_id": (contacts_by_email or {}).get(
                        (participant.get("email") or "").lower()
                    ),
                    "user_id": (members_by_email or {}).get(
                        (participant.get("email") or "").lower()
                    ),
                }
                for participant in (row.participants or [])
            ],
            "source": row.source,
            "gmail_thread_id": row.gmail_thread_id,
            "conversation_id": row.conversation_id,
            "conversation_count": conversation_count,
            "deep_link": row.deep_link,
            "created_at": row.created_at,
        }


class InteractionKindService:
    """CRUD for tenant-configurable interaction kinds (#174), gated on
    ``interactions.kind.manage`` — the contact-types / leave-types shape.

    Defaults seed lazily, once per org, the way leave types do: the first list (or manual
    write) by someone who can log interactions creates the five system kinds. ``email`` is
    protected — relabel it, never delete or deactivate it, because the gmail feed keeps
    writing rows of that kind regardless of what the tenant configures.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(InteractionKindDef)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def list(self, *, include_inactive: bool = False) -> list[InteractionKindDef]:
        await self._ensure_defaults()
        stmt = self.repo.scoped_select().order_by(
            InteractionKindDef.position, InteractionKindDef.key
        )
        if not include_inactive:
            stmt = stmt.where(InteractionKindDef.active.is_(True))
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def active_keys(self) -> set[str]:
        await self._ensure_defaults()
        stmt = select(InteractionKindDef.key).where(
            InteractionKindDef.org_id == self._org_id, InteractionKindDef.active.is_(True)
        )
        return set((await self.ctx.session.execute(stmt)).scalars())

    async def _ensure_defaults(self) -> None:
        """Ensure the system kinds exist for this org (idempotent; skipped for read-only roles).

        Not "seed once when empty": an org seeded during an intermediate state — a single
        ``meeting`` default, before the online/physical split (#174) — must still gain the kinds
        it lacks, or the form never offers *Online afspraak* / *Afspraak op locatie* (#184). So
        reconcile by key: insert any ``DEFAULT_KINDS`` the org is missing and leave every existing
        row (and its tenant relabel) untouched. The ``(org_id, key)`` unique constraint keeps a
        concurrent double-seed from duplicating.
        """
        if not self.ctx.can("interactions.interaction.write"):
            return
        existing = set(
            (
                await self.ctx.session.execute(
                    select(InteractionKindDef.key).where(InteractionKindDef.org_id == self._org_id)
                )
            ).scalars()
        )
        for spec in DEFAULT_KINDS:
            if spec["key"] not in existing:
                await self.repo.create(**spec)

    async def create(self, data: InteractionKindDefCreate) -> InteractionKindDef:
        self.ctx.require("interactions.kind.manage")
        await self._ensure_defaults()
        existing = await self.ctx.session.scalar(
            select(InteractionKindDef.id).where(
                InteractionKindDef.org_id == self._org_id, InteractionKindDef.key == data.key
            )
        )
        if existing is not None:
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"key": "errors.conflict"}
            )
        return await self.repo.create(**data.model_dump(mode="json"))

    async def update(
        self, kind_id: uuid.UUID, data: InteractionKindDefUpdate
    ) -> InteractionKindDef:
        self.ctx.require("interactions.kind.manage")
        row = await self.repo.get_or_404(kind_id)
        values = data.model_dump(mode="json", exclude_unset=True)
        if row.key == PROTECTED_KIND and values.get("active") is False:
            # The gmail feed writes `email` rows whatever the tenant configures — a kind that
            # keeps occurring cannot be switched off, only relabelled.
            raise AppError("conflict", "errors.interactions_kind_protected", status_code=409)
        return await self.repo.update(row, **values)

    async def delete(self, kind_id: uuid.UUID) -> None:
        """Hard-delete only unused kinds; ones with history deactivate instead."""
        self.ctx.require("interactions.kind.manage")
        row = await self.repo.get_or_404(kind_id)
        if row.key == PROTECTED_KIND:
            raise AppError("conflict", "errors.interactions_kind_protected", status_code=409)
        in_use = await self.ctx.session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(Interaction.org_id == self._org_id, Interaction.kind == row.key)
        )
        if int(in_use or 0) > 0:
            raise AppError("conflict", "errors.interactions_kind_in_use", status_code=409)
        await self.repo.delete(row)


async def count_for_entity(ctx: RequestContext, entity_field: str, entity_id: uuid.UUID) -> int:
    """How many interactions attach to one host entity — the panel's truncation counter.

    A contact is counted off the roster, not the lead column (#300), so this answers the same
    question ``?contact_id=`` does — a counter and its list disagreeing is the shape §15 calls
    "a hand-built count".
    """
    if entity_field == "contact_id":
        condition = (
            select(InteractionContact.id)
            .where(
                InteractionContact.org_id == Interaction.org_id,
                InteractionContact.interaction_id == Interaction.id,
                InteractionContact.contact_id == entity_id,
            )
            .exists()
        )
    else:
        condition = getattr(Interaction, entity_field) == entity_id
    return int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(Interaction.org_id == ctx.org.id, condition)
        )
        or 0
    )
