"""Interaction service — feed, manual CRUD, and the gmail review flow (approve/reject/remap).

The review rule, agreed with the user: **only the mailbox owner** decides about their own
gmail-sourced rows. Unlike every other own/any permission, ``interactions.interaction.review``
deliberately has no ``:any`` escalation — an admin must not be able to approve a colleague's
email into the CRM, because the thing being protected is the *mailbox owner's* judgment, not a
capability tier. Manual rows follow the ordinary own/any write/delete scopes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, func, or_, select, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.auth.models import User
from app.core.events import emit
from app.core.models import Membership
from app.core.richtext import extract_mention_ids, sanitize_markdown
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.interactions.models import (
    DEFAULT_KINDS,
    ENTITY_TYPE,
    HOST_ENTITY,
    PROTECTED_KIND,
    Interaction,
    InteractionKindDef,
    InteractionSource,
    InteractionStatus,
)
from app.modules.interactions.schemas import (
    InteractionCreate,
    InteractionKindDefCreate,
    InteractionKindDefUpdate,
    InteractionRemap,
    InteractionUpdate,
)

#: Fields whose edits land in the activity trail (§16) — the record's own definition, not body.
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


class InteractionService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Interaction)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

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
    ) -> tuple[list[dict[str, Any]], int]:
        include_set = {part.strip() for part in (include or "").split(",") if part.strip()}
        conditions = []
        if not self.ctx.can("interactions.interaction.read_all"):
            # Someone else's queue is not a filter you may use (#168)...
            if owner_user_id is not None and owner_user_id != self.ctx.user.id:
                raise AppError("forbidden", "errors.forbidden", status_code=403)
            # ...and a pending row is private to its mailbox owner until approved (#172):
            # every panel and list simply omits other people's pending rows.
            conditions.append(
                or_(
                    Interaction.status != InteractionStatus.PENDING.value,
                    Interaction.owner_user_id == self.ctx.user.id,
                )
            )
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
                conditions.append(
                    or_(own, Interaction.task_id.in_(task_ids)) if task_ids else own
                )
            else:
                conditions.append(Interaction.project_id == project_id)
        if task_id is not None:
            conditions.append(Interaction.task_id == task_id)
        if contact_id is not None:
            conditions.append(Interaction.contact_id == contact_id)
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
        stmt = (
            select(Interaction, User.full_name, User.email)
            .outerjoin(User, User.id == Interaction.owner_user_id)
            .where(Interaction.org_id == self._org_id, *conditions)
            .order_by(Interaction.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        total = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(Interaction)
                .where(Interaction.org_id == self._org_id, *conditions)
            )
            or 0
        )
        plain_rows = [row for row, _, _ in rows]
        names = await self._link_names(plain_rows)
        contacts_by_email = await self._participant_contacts(plain_rows)
        members_by_email = await self._participant_members(plain_rows)
        return [
            self._present(row, full_name, email, names, contacts_by_email, members_by_email)
            for row, full_name, email in rows
        ], total

    async def get(self, interaction_id: uuid.UUID) -> dict[str, Any]:
        row = await self.repo.get_or_404(interaction_id)
        # A pending row is its owner's alone until approved (#172) — absent, not forbidden,
        # so the id leaks nothing (§15).
        if (
            row.status == InteractionStatus.PENDING.value
            and row.owner_user_id != self.ctx.user.id
            and not self.ctx.can("interactions.interaction.read_all")
        ):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return await self._present_one(row)

    # --- manual writes ---------------------------------------------------------- #
    async def create(self, data: InteractionCreate) -> dict[str, Any]:
        self.ctx.require("interactions.interaction.write")
        await self._require_manual_kind(data.kind)
        links = await self._resolve_links(
            {
                "company_id": data.company_id,
                "project_id": data.project_id,
                "task_id": data.task_id,
                "contact_id": data.contact_id,
            }
        )
        user = self.ctx.user
        body = sanitize_markdown(data.body_text)
        mentioned = await self._valid_mentions(extract_mention_ids(body))
        row = await self.repo.create(
            kind=data.kind,
            status=InteractionStatus.LOGGED.value,
            occurred_at=await self._as_instant(data.occurred_at),
            subject=data.subject.strip(),
            body_text=body,
            direction=data.direction.value,
            owner_user_id=user.id,
            owner_name=user.full_name or user.email,
            participants=[p.model_dump() for p in data.participants],
            mentioned_user_ids=[str(uid) for uid in mentioned],
            source=InteractionSource.MANUAL.value,
            **links,
        )
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, row.id)
        await self._record_on_hosts(row, "interaction.logged")
        await self._notify_mentions(row, mentioned)
        if data.log_time is not None:
            await self._log_time(row, data.log_time)
        return await self._present_one(row)

    async def _log_time(self, row: Interaction, log_time: Any) -> None:
        """The "Voeg aan mijn uren toe" ride-along (#175): a linked time entry, in this same
        transaction, through the time module's published surface (§6) — never its internals.
        Carries the interaction's own links and subject; typed after the interaction's kind
        when the org has an entry type of the same key (#176), untyped otherwise."""
        self.ctx.require("time.entry.write")
        from app.modules.time import system as time_system

        entry_type_key = await time_system.active_type_key(self.ctx, row.kind)
        await time_system.record_entry(
            self.ctx,
            user_id=self.ctx.user.id,
            started_at=log_time.started_at,
            ended_at=log_time.ended_at,
            company_id=row.company_id,
            project_id=row.project_id,
            task_id=row.task_id,
            description=row.subject,
            entry_type_key=entry_type_key,
            interaction_id=row.id,
        )

    async def update(self, interaction_id: uuid.UUID, data: InteractionUpdate) -> dict[str, Any]:
        row = await self._writable_or_404(interaction_id, "interactions.interaction.write")
        self._manual_only(row)
        before = snapshot(row, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        # Keeping the row's own kind is always allowed — a deactivated kind must not brick
        # editing the rows that already carry it (#174).
        if sent.get("kind") is not None and sent["kind"] != row.kind:
            await self._require_manual_kind(sent["kind"])
        link_updates = {k: sent[k] for k in _LINK_TABLES if k in sent}
        values: dict[str, Any] = {
            k: v for k, v in sent.items() if k not in _LINK_TABLES and k != "participants"
        }
        if values.get("direction") is not None:
            values["direction"] = values["direction"].value
        if "subject" in values and values["subject"]:
            values["subject"] = values["subject"].strip()
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
            mentioned = await self._valid_mentions(extract_mention_ids(body))
            values["mentioned_user_ids"] = [str(uid) for uid in mentioned]
            newly_mentioned = [uid for uid in mentioned if str(uid) not in already]
        values.update(await self._resolve_links(link_updates, partial=True))
        old_links = {field: getattr(row, field) for field in HOST_ENTITY}
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, snapshot(row, _AUDITED_FIELDS)
        )
        await self._record_link_moves(row, old_links)
        await self._notify_mentions(row, newly_mentioned)
        return await self._present_one(row)

    async def delete(self, interaction_id: uuid.UUID) -> None:
        row = await self._writable_or_404(interaction_id, "interactions.interaction.delete")
        self._manual_only(row)
        await self.repo.delete(row)

    # --- gmail review flow (owner-only, no :any escape) ------------------------- #
    async def approve(self, interaction_id: uuid.UUID) -> dict[str, Any]:
        row = await self._owned_gmail_or_404(interaction_id)
        if row.status != InteractionStatus.PENDING.value:
            raise AppError("invalid_state", "errors.interactions_not_pending", status_code=409)
        row = await self.repo.update(row, status=InteractionStatus.LOGGED.value)
        await ActivityService(self.ctx).record(ENTITY_TYPE, row.id, "approved")
        # Approval is the moment the email becomes team-visible — that is when the host
        # records hear about it (#152); a pending row must not announce itself.
        await self._record_on_hosts(row, "interaction.logged")
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
        return await self._present_one(row)

    async def reject(self, interaction_id: uuid.UUID, *, suppress_thread: bool = False) -> None:
        """The owner keeps this email out of the CRM: metadata removed, message suppressed."""
        row = await self._owned_gmail_or_404(interaction_id)
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
        before = snapshot(row, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        if not sent:
            return await self._present_one(row)
        values = await self._resolve_links(sent, partial=True)
        old_links = {field: getattr(row, field) for field in HOST_ENTITY}
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, snapshot(row, _AUDITED_FIELDS)
        )
        await self._record_link_moves(row, old_links)
        # Remapping is the owner engaging with the row — enough to retire the "waiting on
        # your review" notification about it (#170). Bus-only, like approve/reject.
        await emit(
            "interaction.remapped",
            self.ctx,
            {"interaction_id": row.id, "owner_user_id": row.owner_user_id},
        )
        return await self._present_one(row)

    # --- helpers ---------------------------------------------------------------- #
    async def _record_on_hosts(self, row: Interaction, action: str) -> None:
        """Mirror a milestone onto every linked host record's trail (#152), in the same
        transaction. A mirror *event* carrying a pointer — the field-level diff stays on the
        interaction's own trail, so nothing is audited twice."""
        activity = ActivityService(self.ctx)
        payload = {"interaction_id": str(row.id), "kind": row.kind, "subject": row.subject}
        for field, entity_type in HOST_ENTITY.items():
            target_id = getattr(row, field)
            if target_id is not None:
                await activity.record(entity_type, target_id, action, payload)

    async def _record_link_moves(
        self, row: Interaction, old_links: dict[str, uuid.UUID | None]
    ) -> None:
        """A moved contactmoment tells both sides (#152): the host it left and the one it
        joined. Only team-visible (logged) rows announce themselves."""
        if row.status != InteractionStatus.LOGGED.value:
            return
        activity = ActivityService(self.ctx)
        payload = {"interaction_id": str(row.id), "kind": row.kind, "subject": row.subject}
        for field, entity_type in HOST_ENTITY.items():
            old, new = old_links[field], getattr(row, field)
            if old == new:
                continue
            if old is not None:
                await activity.record(entity_type, old, "interaction.unlinked", payload)
            if new is not None:
                await activity.record(entity_type, new, "interaction.linked", payload)

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
                "subject": row.subject,
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

    def _manual_only(self, row: Interaction) -> None:
        """Gmail rows change through the review flow, never through plain edit/delete."""
        if row.source != InteractionSource.MANUAL.value:
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
        if row.source != InteractionSource.GMAIL.value:
            raise AppError("invalid_state", "errors.interactions_manual_no_review", status_code=409)
        if row.owner_user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.interactions_owner_only", status_code=403)
        return row

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
            if target_id is not None:
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
    _LINK_LABELS = {
        "company_id": ("companies", "name"),
        "project_id": ("projects", "name"),
        "task_id": ("tasks", "title"),
        "contact_id": ("contacts", "trim(concat(first_name, ' ', coalesce(last_name, '')))"),
    }

    async def _link_names(
        self, rows: list[Interaction]
    ) -> dict[tuple[str, uuid.UUID], str]:
        """Labels for the linked records (#147) — one batched query per referenced table for
        the whole page, never a per-row lookup (docs/PERFORMANCE.md). Raw ids are worse than
        saying nothing, and the web should not need four lookup fetches to draw a chip."""
        wanted: dict[str, set[uuid.UUID]] = {field: set() for field in _LINK_TABLES}
        for row in rows:
            for field in _LINK_TABLES:
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
            result = await self.ctx.session.execute(
                stmt, {"oid": self._org_id, "ids": list(ids)}
            )
            for target_id, target_label in result:
                names[(field, target_id)] = target_label
        return names

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
        contacts_by_email = await self._participant_contacts([row])
        members_by_email = await self._participant_members([row])
        return self._present(
            row,
            owner[0] if owner else None,
            owner[1] if owner else None,
            names,
            contacts_by_email,
            members_by_email,
        )

    async def _participant_contacts(self, rows: list[Interaction]) -> dict[str, uuid.UUID]:
        """Which participant addresses exist as org contacts (#160) — one batched,
        org-scoped query over the page's distinct emails, matched at read time so a contact
        created after the email was logged still links up. Display data, never authz."""
        emails: set[str] = set()
        for row in rows:
            for participant in row.participants or []:
                email = (participant.get("email") or "").lower()
                if email:
                    emails.add(email)
        if not emails:
            return {}
        stmt = text(
            "SELECT lower(email), id FROM contacts WHERE org_id = :oid AND lower(email) IN :emails"
        ).bindparams(bindparam("emails", expanding=True))
        result = await self.ctx.session.execute(
            stmt, {"oid": self._org_id, "emails": list(emails)}
        )
        return dict(result.all())

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
    ) -> dict[str, Any]:
        """Owner resolved like the activity trail (issue #64): live account wins, snapshot after."""
        if live_email is not None:
            owner_name, owner_deleted = live_name or live_email, False
        else:
            owner_name = row.owner_name
            owner_deleted = row.owner_name is not None
        names = names or {}
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "occurred_at": row.occurred_at,
            "subject": row.subject,
            "snippet": row.snippet,
            "body_text": row.body_text,
            "direction": row.direction,
            "company_id": row.company_id,
            "project_id": row.project_id,
            "task_id": row.task_id,
            "contact_id": row.contact_id,
            "company_name": names.get(("company_id", row.company_id)),
            "project_name": names.get(("project_id", row.project_id)),
            "task_title": names.get(("task_id", row.task_id)),
            "contact_name": names.get(("contact_id", row.contact_id)),
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
        """Seed the system kinds once per org (idempotent; skipped for read-only roles)."""
        if not self.ctx.can("interactions.interaction.write"):
            return
        if await self.repo.count() > 0:
            return
        for spec in DEFAULT_KINDS:
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


async def count_for_entity(
    ctx: RequestContext, entity_field: str, entity_id: uuid.UUID
) -> int:
    """How many interactions attach to one host entity — the panel's truncation counter."""
    column = getattr(Interaction, entity_field)
    return int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(Interaction.org_id == ctx.org.id, column == entity_id)
        )
        or 0
    )
