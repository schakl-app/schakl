"""ARQ jobs for microsoft.outlook: the poll cron, per-mailbox poll worker, and the body sweep.

Every cron but the reaper first checks the license is still writable for the ``microsoft`` sku:
the mount-time 402 gate covers requests, but crons write on a schedule (issue #137 semantics).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.entitlements.service import sku_cron_enabled
from app.core.events import SystemContext
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection
from app.integrations.microsoft.oauth import has_mail_scope, microsoft_settings_row
from app.integrations.microsoft.outlook.service import (
    SOURCE,
    fetch_body,
    poll_connection,
    reap_skips,
)


async def _licensed() -> bool:
    return await sku_cron_enabled("microsoft")


async def outlook_poll(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: offer each opted-in mailbox to the poll worker."""
    if not await _licensed():
        return

    async def _offer(org, session) -> None:
        row = await microsoft_settings_row(session, org.id)
        if row is None or not row.outlook_enabled:
            return
        connections = (
            (
                await session.execute(
                    select(MicrosoftConnection).where(
                        MicrosoftConnection.org_id == org.id,
                        MicrosoftConnection.status == ConnectionStatus.ACTIVE.value,
                        MicrosoftConnection.outlook_sync_enabled,
                    )
                )
            )
            .scalars()
            .all()
        )
        for connection in connections:
            if has_mail_scope(connection.scopes):
                await enqueue("outlook_poll_connection", str(org.id), str(connection.id))

    await run_per_org(_offer)


async def outlook_sweep_bodies(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: approved emails whose body fetch never landed — the row is its own outbox."""
    if not await _licensed():
        return

    async def _sweep(org, session) -> None:
        from app.modules.interactions import system as interactions_system

        row = await microsoft_settings_row(session, org.id)
        if row is None or not row.outlook_enabled:
            return
        sys_ctx = SystemContext(org=org, session=session)
        for interaction_id in await interactions_system.bodyless_logged_email_ids(
            sys_ctx, source=SOURCE
        ):
            await enqueue("outlook_fetch_body", str(org.id), str(interaction_id))

    await run_per_org(_sweep)


async def outlook_reap_skips(ctx: dict) -> None:  # noqa: ARG001
    """Daily: drop ``outlook_skips`` rows past their retention window. Deliberately **not**
    gated on the licence — reaping is deletion, and an expired licence must not turn a
    retention promise into an indefinite one."""
    await run_per_org(reap_skips)


async def outlook_poll_connection(ctx: dict, org_id: str, connection_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        connection = await session.scalar(
            select(MicrosoftConnection).where(
                MicrosoftConnection.org_id == oid,
                MicrosoftConnection.id == uuid.UUID(connection_id),
            )
        )
        if org is None or connection is None:
            return "gone"
        logged = await poll_connection(session, org, connection)
        await session.commit()
    return f"logged:{logged}"


async def outlook_fetch_body(ctx: dict, org_id: str, interaction_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        if org is None:
            return "gone"
        fetched = await fetch_body(session, org, uuid.UUID(interaction_id))
        await session.commit()
    return "fetched" if fetched else "skipped"
