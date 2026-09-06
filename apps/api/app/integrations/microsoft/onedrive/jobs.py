"""ARQ jobs for microsoft.onedrive: the provisioning worker and its sweep cron.

Every cron first checks the license is still writable for the ``microsoft`` sku: the
mount-time 402 gate covers requests, but crons write on a schedule — an expired license must
stop the background half too (issue #137 semantics: expired = read-only, not gone).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.onedrive.models import FolderJobStatus, OneDriveFolderJob
from app.integrations.microsoft.onedrive.service import MAX_ATTEMPTS, provision_folder


async def _licensed() -> bool:
    return await sku_cron_enabled("microsoft")


async def onedrive_sweep_folder_jobs(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: re-offer pending folder jobs whose enqueue was lost or that failed."""
    if not await _licensed():
        return

    async def _sweep(org, session) -> None:
        jobs = (
            (
                await session.execute(
                    select(OneDriveFolderJob.id).where(
                        OneDriveFolderJob.org_id == org.id,
                        OneDriveFolderJob.status == FolderJobStatus.PENDING.value,
                        OneDriveFolderJob.attempts < MAX_ATTEMPTS,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job_id in jobs:
            await enqueue("onedrive_provision_folder", str(org.id), str(job_id))

    await run_per_org(_sweep)


async def onedrive_provision_folder(ctx: dict, org_id: str, job_id: str) -> str:  # noqa: ARG001
    """The org id rides along from the enqueue site — RLS is fail-closed, so a worker can never
    look a row's tenant up from the row itself."""
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        job = await session.scalar(
            select(OneDriveFolderJob).where(
                OneDriveFolderJob.org_id == oid, OneDriveFolderJob.id == uuid.UUID(job_id)
            )
        )
        if org is None or job is None:
            return "gone"
        await provision_folder(session, org, job)
        await session.commit()
    return "done"
