"""ARQ jobs for google.drive: the provisioning worker and its sweep cron."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.entitlements.service import license_state
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.modules.google.drive.models import DriveFolderJob, FolderJobStatus
from app.modules.google.drive.service import MAX_ATTEMPTS, provision_folder


async def _licensed() -> bool:
    return (await license_state()).writable("google")


async def google_drive_sweep_folder_jobs(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: re-offer pending folder jobs whose enqueue was lost or that failed."""
    if not await _licensed():
        return

    async def _sweep(org, session) -> None:
        jobs = (
            (
                await session.execute(
                    select(DriveFolderJob.id).where(
                        DriveFolderJob.org_id == org.id,
                        DriveFolderJob.status == FolderJobStatus.PENDING.value,
                        DriveFolderJob.attempts < MAX_ATTEMPTS,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job_id in jobs:
            await enqueue("google_drive_provision_folder", str(org.id), str(job_id))

    await run_per_org(_sweep)


async def google_drive_provision_folder(ctx: dict, org_id: str, job_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        job = await session.scalar(
            select(DriveFolderJob).where(
                DriveFolderJob.org_id == oid, DriveFolderJob.id == uuid.UUID(job_id)
            )
        )
        if org is None or job is None:
            return "gone"
        await provision_folder(session, org, job)
        await session.commit()
    return "done"
