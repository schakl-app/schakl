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

from sqlalchemy import func, select, text

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
    ENTITY_TYPE,
    MANUAL_KINDS,
    Interaction,
    InteractionSource,
    InteractionStatus,
)
from app.modules.interactions.schemas import (
    InteractionCreate,
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
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Interaction.company_id == company_id)
        if project_id is not None:
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
        return [self._present(row, full_name, email) for row, full_name, email in rows], total

    async def get(self, interaction_id: uuid.UUID) -> dict[str, Any]:
        row = await self.repo.get_or_404(interaction_id)
        return await self._present_one(row)

    # --- manual writes ---------------------------------------------------------- #
    async def create(self, data: InteractionCreate) -> dict[str, Any]:
        self.ctx.require("interactions.interaction.write")
        if data.kind not in MANUAL_KINDS:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"kind": "errors.interactions_kind_not_manual"},
            )
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
            kind=data.kind.value,
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
        await self._notify_mentions(row, mentioned)
        return await self._present_one(row)

    async def update(self, interaction_id: uuid.UUID, data: InteractionUpdate) -> dict[str, Any]:
        row = await self._writable_or_404(interaction_id, "interactions.interaction.write")
        self._manual_only(row)
        before = snapshot(row, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        if "kind" in sent and data.kind not in MANUAL_KINDS:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"kind": "errors.interactions_kind_not_manual"},
            )
        link_updates = {k: sent[k] for k in _LINK_TABLES if k in sent}
        values: dict[str, Any] = {
            k: v for k, v in sent.items() if k not in _LINK_TABLES and k != "participants"
        }
        for enum_field in ("kind", "direction"):
            if enum_field in values and values[enum_field] is not None:
                values[enum_field] = values[enum_field].value
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
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, snapshot(row, _AUDITED_FIELDS)
        )
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
        row = await self.repo.update(row, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, row.id, before, snapshot(row, _AUDITED_FIELDS)
        )
        return await self._present_one(row)

    # --- helpers ---------------------------------------------------------------- #
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
        return self._present(row, owner[0] if owner else None, owner[1] if owner else None)

    def _present(
        self, row: Interaction, live_name: str | None, live_email: str | None
    ) -> dict[str, Any]:
        """Owner resolved like the activity trail (issue #64): live account wins, snapshot after."""
        if live_email is not None:
            owner_name, owner_deleted = live_name or live_email, False
        else:
            owner_name = row.owner_name
            owner_deleted = row.owner_name is not None
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
            "owner_user_id": row.owner_user_id,
            "owner_name": owner_name,
            "owner_deleted": owner_deleted,
            "participants": row.participants,
            "source": row.source,
            "gmail_thread_id": row.gmail_thread_id,
            "deep_link": row.deep_link,
            "created_at": row.created_at,
        }


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
