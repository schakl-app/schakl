"""ARQ jobs for google.gmail: the poll cron, per-mailbox poll worker, and the body sweep."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.entitlements.service import license_state
from app.core.events import SystemContext
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.modules.google.gmail.service import fetch_body, poll_connection
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.google.oauth import SCOPE_GMAIL, google_settings_row


async def _licensed() -> bool:
    return (await license_state()).writable("google")


async def google_gmail_poll(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: offer each opted-in mailbox to the poll worker."""
    if not await _licensed():
        return

    async def _offer(org, session) -> None:
        row = await google_settings_row(session, org.id)
        if row is None or not row.gmail_enabled:
            return
        connections = (
            (
                await session.execute(
                    select(GoogleConnection).where(
                        GoogleConnection.org_id == org.id,
                        GoogleConnection.status == ConnectionStatus.ACTIVE.value,
                        GoogleConnection.gmail_sync_enabled,
                    )
                )
            )
            .scalars()
            .all()
        )
        for connection in connections:
            if SCOPE_GMAIL in (connection.scopes or []):
                await enqueue(
                    "google_gmail_poll_connection", str(org.id), str(connection.id)
                )

    await run_per_org(_offer)


async def google_gmail_sweep_bodies(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: approved emails whose body fetch never landed — the row is its own outbox."""
    if not await _licensed():
        return

    async def _sweep(org, session) -> None:
        from app.modules.interactions import system as interactions_system

        row = await google_settings_row(session, org.id)
        if row is None or not row.gmail_enabled:
            return
        sys_ctx = SystemContext(org=org, session=session)
        for interaction_id in await interactions_system.bodyless_logged_email_ids(sys_ctx):
            await enqueue("google_gmail_fetch_body", str(org.id), str(interaction_id))

    await run_per_org(_sweep)


async def google_gmail_poll_connection(ctx: dict, org_id: str, connection_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        connection = await session.scalar(
            select(GoogleConnection).where(
                GoogleConnection.org_id == oid,
                GoogleConnection.id == uuid.UUID(connection_id),
            )
        )
        if org is None or connection is None:
            return "gone"
        logged = await poll_connection(session, org, connection)
        await session.commit()
    return f"logged:{logged}"


async def google_gmail_fetch_body(ctx: dict, org_id: str, interaction_id: str) -> str:  # noqa: ARG001
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
