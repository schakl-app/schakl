"""Content-addressed blobs — the one place a write decides whether bytes are new.

A ``files`` row used to own its object outright, so the same signature logo arriving on 500
e-mails was 500 objects. Here a write first asks *"do I already hold these bytes?"*, identified
by their sha256, and only uploads on a miss.

Three rules hold it up.

**The reservation is one statement, and it is the lock.** ``reserve`` is a single
``INSERT … ON CONFLICT DO UPDATE … RETURNING (xmax = 0)``: it tells you the key to write to and
whether you are the one who must write it, in one round trip, while holding the row lock that
the sweeper (``jobs.py``) contends for. Two callers storing identical bytes at the same instant
serialize on it; exactly one is told to upload.

**The key is the content**, ``<org_id>/sha256/<hex>`` — so even if two writers were somehow
both told to upload, they write identical bytes to the same key. **Per org, never across
orgs** (Golden Rule 1): terminating a tenant still reclaims exactly its own key space, and no
tenant's bytes are ever reachable from another's. The saving is within one agency's mailbox,
which is where the duplicates actually are.

**Reads never come here.** ``files.backend``/``files.storage_key`` are mirrored from the blob on
every write, so serving a file is the same single row read it always was — no join, no second
query (docs/PERFORMANCE.md) — and a rolled-back release still finds every file from the row
alone (docs/WORKFLOW.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import delete, func, literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.storage.models import FileBlob

#: Read the stream in chunks rather than whole: an upload is already spooled to disk past a
#: small threshold, and hashing it must not be the thing that loads it into memory.
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class BlobReservation:
    """What a write needs to know: where the bytes belong, and whether it must put them."""

    id: uuid.UUID
    backend: str
    storage_key: str
    size_bytes: int
    #: ``True`` when this call created the blob row — the caller, and only the caller, must
    #: now write the bytes. ``False`` is a de-duplication hit: the object is already there,
    #: and on S3 that skips a whole upload round trip.
    needs_bytes: bool


def content_key(org_id: uuid.UUID, digest: str) -> str:
    """Where new bytes live. Content-addressed, and under the org's own prefix."""
    return f"{org_id}/sha256/{digest}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def digest_stream(stream: BinaryIO) -> tuple[str, int]:
    """The sha256 and true size of a seekable stream, which is left rewound.

    The size is *measured*, never taken from the caller: a multipart ``Content-Length`` is the
    client's claim about the upload, and it is the number the size ceiling is checked against.
    """
    return await asyncio.to_thread(digest_reader, stream)


def digest_reader(stream: BinaryIO) -> tuple[str, int]:
    """Blocking sha256 + size of an open reader. Call it in a thread, or from a worker."""
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    while chunk := stream.read(_CHUNK):
        digest.update(chunk)
        size += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), size


async def reserve(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    sha256: str,
    size_bytes: int,
    backend: str | None = None,
    storage_key: str | None = None,
) -> BlobReservation:
    """Claim the blob for these bytes, creating it if this org has never held them.

    ``storage_key`` is for the maintenance job **adopting** a pre-dedup file: the blob takes
    over that file's existing key, because adopting is a row write and must never be a byte
    copy. Every other caller leaves it unset and gets the content-addressed key.

    Clearing ``unreferenced_since`` is what resurrects a blob the sweeper had marked for
    collection: the sweeper only reclaims a blob whose stamp survived its own re-check, so a
    write that lands first keeps the bytes alive without either side needing to know about the
    other.
    """
    resolved_backend = backend or settings.storage_backend
    stmt = (
        pg_insert(FileBlob)
        .values(
            id=uuid.uuid4(),
            org_id=org_id,
            backend=resolved_backend,
            sha256=sha256,
            storage_key=storage_key or content_key(org_id, sha256),
            size_bytes=size_bytes,
        )
        .on_conflict_do_update(
            constraint="uq_file_blobs_org_backend_sha",
            set_={"unreferenced_since": None, "updated_at": func.now()},
        )
        .returning(
            FileBlob.id,
            FileBlob.backend,
            FileBlob.storage_key,
            FileBlob.size_bytes,
            # Postgres leaves ``xmax`` zero on a freshly inserted tuple and stamps it with the
            # updating transaction otherwise — the only way to learn insert-vs-update without
            # a second statement, and a second statement is exactly the race this avoids.
            literal_column("(xmax = 0)").label("inserted"),
        )
    )
    row = (await session.execute(stmt)).one()
    return BlobReservation(
        id=row.id,
        backend=row.backend,
        storage_key=row.storage_key,
        size_bytes=row.size_bytes,
        needs_bytes=bool(row.inserted),
    )


async def release(session: AsyncSession, blob_id: uuid.UUID) -> None:
    """Undo a reservation whose bytes never landed (an upload that raised).

    Best-effort: the row is only deletable while nothing references it, which is exactly the
    case here, and the sweeper would collect it anyway. Doing it now matters because the
    reservation may already be **committed** — an S3 put runs inside ``release_db()`` — so
    without this a failed upload would leave a blob that the next write de-duplicates onto and
    then serves as missing bytes.
    """
    await session.execute(delete(FileBlob).where(FileBlob.id == blob_id))
