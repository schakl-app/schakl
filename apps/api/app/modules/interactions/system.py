"""System-actor interaction writes — the published surface the gmail feed writes through.

The licensed ``google`` module's poller runs as a ``SystemContext`` (no person, wildcard-free:
authorization happened when the mailbox owner connected Gmail and enabled logging). It never
touches ``Interaction`` internals directly — these helpers are the boundary (§6), exactly like
``tasks.system`` is for automation. Everything here is tenant-scoped through the context's
session, whose RLS GUC is already bound.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.activity import ActivityService
from app.core.events import EmitContext
from app.modules.interactions.models import (
    HOST_ENTITY,
    Interaction,
    InteractionKind,
    InteractionSource,
    InteractionStatus,
)

#: The mapping fields thread inheritance copies from an earlier message in the thread.
MAPPING_FIELDS = ("company_id", "project_id", "task_id", "contact_id")


async def record_email(
    ctx: EmitContext,
    *,
    owner_user_id: uuid.UUID,
    owner_name: str | None,
    occurred_at: datetime,
    subject: str | None,
    snippet: str | None,
    direction: str,
    participants: list[dict[str, Any]],
    gmail_message_id: str,
    gmail_thread_id: str | None,
    rfc822_message_id: str | None,
    deep_link: str | None,
    pending: bool,
    mappings: dict[str, uuid.UUID | None],
) -> Interaction:
    """Insert one matched email. The caller decided status, dedup and mappings already."""
    # A row auto-approved at birth joins its thread's conversation now (#272); a pending row
    # gets its id later, when its owner approves it (the service does that). Grouping is only
    # ever set on logged rows, so a pending row keeps ``NULL`` and folds to itself.
    conversation_id = (
        await resolve_conversation_id(ctx, gmail_thread_id) if not pending else None
    )
    row = Interaction(
        org_id=ctx.org.id,
        kind=InteractionKind.EMAIL.value,
        status=(InteractionStatus.PENDING if pending else InteractionStatus.LOGGED).value,
        occurred_at=occurred_at,
        subject=(subject or "")[:500] or None,
        snippet=snippet,
        direction=direction,
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        participants=participants,
        source=InteractionSource.GMAIL.value,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
        rfc822_message_id=rfc822_message_id,
        deep_link=deep_link,
        conversation_id=conversation_id,
        **{field: mappings.get(field) for field in MAPPING_FIELDS},
    )
    ctx.session.add(row)
    await ctx.session.flush()
    if not pending:
        # Auto-approved at birth (trusted thread / auto-approve policy): the host records
        # hear about it now (#152), attributed to the system — a pending row stays silent
        # until the owner approves it (the service records that moment).
        activity = ActivityService(ctx)
        payload = {"interaction_id": str(row.id), "kind": row.kind, "subject": row.subject}
        for field, entity_type in HOST_ENTITY.items():
            target_id = getattr(row, field)
            if target_id is not None:
                await activity.record(entity_type, target_id, "interaction.logged", payload)
    return row


async def gmail_message_seen(
    ctx: EmitContext, owner_user_id: uuid.UUID, gmail_message_id: str
) -> bool:
    return (
        await ctx.session.scalar(
            select(Interaction.id).where(
                Interaction.org_id == ctx.org.id,
                Interaction.owner_user_id == owner_user_id,
                Interaction.gmail_message_id == gmail_message_id,
            )
        )
    ) is not None


async def rfc822_seen(ctx: EmitContext, rfc822_message_id: str) -> bool:
    """Cross-mailbox dedup: has *any* connected mailbox already logged this email?"""
    return (
        await ctx.session.scalar(
            select(Interaction.id).where(
                Interaction.org_id == ctx.org.id,
                Interaction.rfc822_message_id == rfc822_message_id,
            )
        )
    ) is not None


async def thread_mappings(
    ctx: EmitContext, gmail_thread_id: str
) -> dict[str, uuid.UUID | None] | None:
    """The newest *logged* mapping in this thread, if any — what a follow-up inherits."""
    row = (
        await ctx.session.execute(
            select(Interaction)
            .where(
                Interaction.org_id == ctx.org.id,
                Interaction.gmail_thread_id == gmail_thread_id,
                Interaction.status == InteractionStatus.LOGGED.value,
            )
            .order_by(Interaction.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    mappings = {field: getattr(row, field) for field in MAPPING_FIELDS}
    return mappings if any(mappings.values()) else None


async def resolve_conversation_id(
    ctx: EmitContext, gmail_thread_id: str | None, *, exclude_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    """The ``conversation_id`` a newly-logged email in this gmail thread should carry (#272):
    reuse the newest existing logged sibling's, or mint one and backfill that sibling so the
    pair becomes a group the moment a second message joins it.

    Returns ``None`` when there is no other logged message in the thread yet — the caller then
    stores ``NULL`` (a singleton that folds to itself). Same "newest logged row in this thread"
    query as :func:`thread_mappings`, plus ``id != exclude_id`` so an approving row never
    matches itself.
    """
    if not gmail_thread_id:
        return None
    conditions = [
        Interaction.org_id == ctx.org.id,
        Interaction.gmail_thread_id == gmail_thread_id,
        Interaction.status == InteractionStatus.LOGGED.value,
    ]
    if exclude_id is not None:
        conditions.append(Interaction.id != exclude_id)
    sibling = (
        await ctx.session.execute(
            select(Interaction)
            .where(*conditions)
            .order_by(Interaction.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sibling is None:
        return None
    if sibling.conversation_id is None:
        sibling.conversation_id = uuid.uuid4()
        await ctx.session.flush()
    return sibling.conversation_id


async def email_ref(
    ctx: EmitContext, interaction_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    """``(owner_user_id, gmail_message_id)`` for a gmail row — what a body fetch needs."""
    row = (
        await ctx.session.execute(
            select(Interaction.owner_user_id, Interaction.gmail_message_id).where(
                Interaction.org_id == ctx.org.id, Interaction.id == interaction_id
            )
        )
    ).first()
    if row is None or row[0] is None or not row[1]:
        return None
    return row[0], row[1]


async def set_body(ctx: EmitContext, interaction_id: uuid.UUID, body_text: str) -> None:
    """The async body fetch landing (post-approval); a since-rejected row is a silent no-op."""
    row = (
        await ctx.session.execute(
            select(Interaction).where(
                Interaction.org_id == ctx.org.id, Interaction.id == interaction_id
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.body_text = body_text
        await ctx.session.flush()


async def bodyless_logged_email_ids(ctx: EmitContext, limit: int = 50) -> list[uuid.UUID]:
    """Approved emails whose body fetch hasn't landed — the row is its own outbox."""
    rows = (
        await ctx.session.execute(
            select(Interaction.id)
            .where(
                Interaction.org_id == ctx.org.id,
                Interaction.source == InteractionSource.GMAIL.value,
                Interaction.status == InteractionStatus.LOGGED.value,
                Interaction.body_text.is_(None),
                Interaction.gmail_message_id.is_not(None),
            )
            .limit(limit)
        )
    ).all()
    return [row[0] for row in rows]
