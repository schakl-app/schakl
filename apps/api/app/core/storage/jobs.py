"""Storage maintenance — fold what is already duplicated, reclaim what nothing references.

Two passes, one daily per-org cron (``app/worker.py``). They are separate concerns that share a
job because they share a lock: both take the ``file_blobs`` row lock that ``blobs.reserve``
contends for, and that lock is the whole concurrency story.

**Fold** is the retroactive half. De-duplication only helps writes made after it shipped, so
this hashes the pre-dedup rows (``blob_id IS NULL``) in bounded batches and collapses identical
content onto one object. It is deliberately *not* a migration: hashing every object an agency
has ever stored is unbounded work, and a self-hosted instance migrates itself unattended on
upgrade (docs/WORKFLOW.md). A batch a night converges within days and never stalls a release.

The first row of a digest **adopts its own existing key** — the blob takes over the key that
file already had. Nothing is copied and nothing is moved, so folding a terabyte costs a read
per object and no writes at all; only the redundant copies are deleted.

**Sweep** is the collector. A file delete no longer touches bytes (another file may hold the
same content and only a whole-table view can tell), so this is the one thing that reclaims
space. It is two-pass on purpose: the first sighting of an unreferenced blob only *stamps* it,
and the bytes go a grace window later. A blob is therefore never collected in the same breath
as the delete that unreferenced it, which is what makes "I deleted the wrong file" recoverable
by inserting a row rather than by restoring a backup.

The sweep holds the row lock across the byte delete. A background job may — the ``release_db``
rule (docs/PERFORMANCE.md) is about not pinning a *request's* pooled connection — and it is
what closes the race: a concurrent write either wins the lock and clears the stamp (the sweep
re-reads it and skips), or waits, finds the row gone, and reserves a fresh blob that it is
then told to upload.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.jobs import run_per_org
from app.core.models import Org
from app.core.storage import blobs
from app.core.storage.backend import StorageUnavailableError, storage_for
from app.core.storage.models import FileBlob, StoredFile

logger = logging.getLogger("schakl.storage")

#: How many unreferenced blobs one run may reclaim per org. Separate from the fold batch: this
#: one is pure deletes and cheap, but it is still an external call each on S3.
_SWEEP_BATCH = 500


async def storage_maintenance(ctx: dict) -> str:
    """The cron entry point: fold, then sweep, for every active org."""
    folded = reclaimed = 0

    async def per_org(org: Org, session: AsyncSession) -> None:
        nonlocal folded, reclaimed
        folded += await fold_legacy_files(session, org)
        reclaimed += await sweep_unreferenced(session, org)

    await run_per_org(per_org)
    if folded or reclaimed:
        logger.info(
            "storage maintenance: folded %s file(s), reclaimed %s blob(s)", folded, reclaimed
        )
    return f"folded={folded} reclaimed={reclaimed}"


async def fold_legacy_files(session: AsyncSession, org: Org) -> int:
    """Give pre-dedup ``files`` rows a blob, collapsing identical content. Returns rows folded.

    Unreadable bytes are logged and skipped rather than fatal: a row whose object is missing
    (volume drift, a backend since removed) is exactly the case that must not stop the rest of
    the batch, and it is already visible as a 404 on download.
    """
    rows = (
        (
            await session.execute(
                select(StoredFile)
                .where(StoredFile.org_id == org.id, StoredFile.blob_id.is_(None))
                .order_by(StoredFile.created_at.asc())
                .limit(settings.storage_fold_batch)
            )
        )
        .scalars()
        .all()
    )
    folded = 0
    for row in rows:
        try:
            backend = storage_for(row.backend)
        except StorageUnavailableError:
            logger.warning(
                "fold: file %s needs backend=%s which is not configured", row.id, row.backend
            )
            continue
        try:
            digest, size = await asyncio.to_thread(_digest_key, backend, row.storage_key)
        except (OSError, ValueError):
            logger.warning(
                "fold: file %s has no readable bytes at backend=%s key=%s",
                row.id,
                row.backend,
                row.storage_key,
            )
            continue
        # Adopt this row's own key: whoever gets here first donates its object to the blob, so
        # folding never copies or moves a byte. A later duplicate reserves the same digest,
        # is told the bytes are already held, and only then gives up its own copy.
        blob = await blobs.reserve(
            session,
            org.id,
            sha256=digest,
            size_bytes=size,
            backend=row.backend,
            storage_key=row.storage_key,
        )
        redundant = None if blob.storage_key == row.storage_key else row.storage_key
        row.blob_id = blob.id
        row.storage_key = blob.storage_key
        row.backend = blob.backend
        row.size_bytes = size
        await session.flush()
        if redundant is not None:
            # Only now, with the row repointed and flushed, is this copy provably redundant.
            await asyncio.to_thread(_delete_quietly, backend, redundant)
        folded += 1
    return folded


async def sweep_unreferenced(session: AsyncSession, org: Org) -> int:
    """Stamp, then reclaim, blobs no ``files`` row references. Returns blobs deleted."""
    cutoff = datetime.now(UTC) - timedelta(hours=settings.storage_blob_grace_hours)
    referenced = exists().where(
        StoredFile.blob_id == FileBlob.id, StoredFile.org_id == org.id
    )
    candidates = (
        (
            await session.execute(
                select(FileBlob)
                .where(FileBlob.org_id == org.id, ~referenced)
                .order_by(FileBlob.unreferenced_since.asc().nulls_last())
                .limit(_SWEEP_BATCH)
                # SKIP LOCKED, so a blob a concurrent write is resurrecting is left for the
                # next run instead of blocking this one behind someone else's upload.
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    reclaimed = 0
    for blob in candidates:
        # Re-read under the lock: between the scan and here a write may have reserved this
        # exact content, which clears the stamp and inserts its row.
        taken = await session.scalar(
            select(
                exists().where(
                    StoredFile.blob_id == blob.id, StoredFile.org_id == org.id
                )
            )
        )
        if taken:
            continue
        if blob.unreferenced_since is None:
            await session.execute(
                update(FileBlob)
                .where(FileBlob.id == blob.id)
                .values(unreferenced_since=func.now())
            )
            continue
        if blob.unreferenced_since > cutoff:
            continue
        try:
            backend = storage_for(blob.backend)
        except StorageUnavailableError:
            logger.warning(
                "sweep: blob %s needs backend=%s which is not configured", blob.id, blob.backend
            )
            continue
        # Inside the lock (see the module docstring): a write racing this one waits, finds the
        # row gone, and is told to upload afresh rather than adopting bytes that are leaving.
        await asyncio.to_thread(_delete_quietly, backend, blob.storage_key)
        await session.execute(delete(FileBlob).where(FileBlob.id == blob.id))
        reclaimed += 1
    return reclaimed


def _digest_key(backend, key: str) -> tuple[str, int]:
    with backend.open(key) as handle:
        return blobs.digest_reader(handle)


def _delete_quietly(backend, key: str) -> None:
    try:
        backend.delete(key)
    except OSError:
        # Orphaned space, never a failed job: the row bookkeeping is the part that must hold.
        logger.warning("storage maintenance: could not delete key %s", key)
