"""The social panel on the company hub (§6): this client's channels, and what is planned.

**What it answers.** Somebody opening a client's page wants two things about their social
media and wants them without leaving: *which channels do we manage here*, and *what is going
out next*. So the payload is the channels, then the posts still going on — soonest first,
because the next one is the one a question is about — and only when those leave room, the
last ones that went out.

**Three statements, whatever the client has** (docs/PERFORMANCE.md): the channels, the posts,
and the posts' deliveries. The per-channel checks a planner row carries are not computed here:
they cost a credential read per token, and a hub composes a dozen panels on one page load.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, select

from app.core.metagraph.assets import KIND_AD_ACCOUNT
from app.core.tenancy import RequestContext
from app.integrations.meta.models import (
    WORKING_POST_STATUSES,
    MetaAsset,
    MetaPost,
    MetaPostTarget,
    PostStatus,
)
from app.integrations.meta.publisher import post_title
from app.registry import PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec

PANEL_LIMIT = 5


async def _social_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    session = ctx.session
    channels = list(
        (
            await session.scalars(
                ctx.repo(MetaAsset)
                .scoped_select()
                .where(
                    MetaAsset.company_id == company_id,
                    MetaAsset.active.is_(True),
                    MetaAsset.kind != KIND_AD_ACCOUNT,
                )
                .order_by(MetaAsset.kind.desc(), MetaAsset.name)
                .limit(12)
            )
        ).all()
    )
    # What is still going on first and soonest first; then what went out, newest first. One
    # statement: the rank decides the block, and each block sorts on its own clock.
    working = MetaPost.status.in_(WORKING_POST_STATUSES)
    posts = list(
        (
            await session.scalars(
                ctx.repo(MetaPost)
                .scoped_select()
                .where(
                    MetaPost.company_id == company_id,
                    MetaPost.status != PostStatus.CANCELLED.value,
                )
                .order_by(
                    case((working, 0), else_=1),
                    case((working, MetaPost.scheduled_at), else_=None).asc().nulls_last(),
                    MetaPost.published_at.desc().nulls_last(),
                    MetaPost.created_at.desc(),
                )
                .limit(PANEL_LIMIT + 1)
            )
        ).all()
    )
    has_more = len(posts) > PANEL_LIMIT
    posts = posts[:PANEL_LIMIT]
    by_post: dict[uuid.UUID, list[str]] = {}
    if posts:
        rows = await session.execute(
            select(MetaPostTarget.post_id, MetaPostTarget.channel)
            .where(
                MetaPostTarget.org_id == ctx.org.id,
                MetaPostTarget.post_id.in_([post.id for post in posts]),
            )
            .order_by(MetaPostTarget.channel)
        )
        for post_id, channel in rows:
            seen = by_post.setdefault(post_id, [])
            if channel not in seen:
                seen.append(channel)
    return {
        "channels": [
            {
                "id": str(row.id),
                "kind": row.kind,
                "name": row.name,
                "username": row.username,
                "picture_url": row.picture_url,
            }
            for row in channels
        ],
        "items": [
            {
                "id": str(post.id),
                "title": post_title(post),
                "status": post.status,
                "format": post.format,
                "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
                "published_at": post.published_at.isoformat() if post.published_at else None,
                "channels": by_post.get(post.id, []),
            }
            for post in posts
        ],
        "has_more": has_more,
    }


meta_company_panel = PanelSpec(
    key="meta.company",
    entity_type="company",
    title_key="meta.panel.title",
    provider=_social_provider,
    # Beside the marketing panels: what is said sits with what is measured.
    position=46,
    requires_permission="meta.asset.read",
    # A working surface: a planned post is something somebody still has to do something
    # about, which a register is not.
    prominence=PROMINENCE_PRIMARY,
    size=SIZE_HALF,
    # Nothing linked and nothing written: one chip in the hub's strip, not a card that is a
    # heading over "no posts".
    empty_when=lambda data: not data.get("channels") and not data.get("items"),
)
