"""Complete org archives: rows **and** stored bytes (epic #199).

``export_org`` dumps rows, and a ``files`` row is a pointer. Once bytes live in object storage
a JSON-only export is not the "provably complete export" that ``purge_org`` demands before it
destroys anything — so the automated termination archives this instead.

The second thing pinned here is the re-keying on import. A ``files`` row carries
``<org_id>/<file_id>``; copying it verbatim into a new org left that org reading out of the
*source* org's key space, which is a cross-tenant read through a legitimate-looking path and
breaks outright the moment either org is terminated (whose purge now deletes that prefix).
"""

from __future__ import annotations

import io
import uuid

from sqlalchemy import select

from app.config import settings
from app.core.instance import portability
from app.core.models import Org
from app.core.storage.backend import get_storage, storage_for
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from tests.conftest import make_tenant


async def _seed_file(org_id: uuid.UUID, payload: bytes) -> uuid.UUID:
    file_id = uuid.uuid4()
    key = f"{org_id}/{file_id}"
    get_storage().put(key, io.BytesIO(payload))
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        session.add(
            StoredFile(
                id=file_id,
                org_id=org_id,
                backend=settings.storage_backend,
                storage_key=key,
                filename="brief.txt",
                content_type="text/plain",
                size_bytes=len(payload),
            )
        )
        await session.commit()
    return file_id


async def test_archive_round_trip_carries_bytes_and_rekeys(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    source = await make_tenant("arch-src")
    file_id = await _seed_file(source.org.id, b"hello bytes")

    async with async_session_maker() as session:
        org = await session.get(Org, source.org.id)
        blob = await portability.build_archive(session, org)

    payload, blobs = portability.read_archive(blob)
    assert payload["files_missing"] == []
    assert blobs[str(file_id)] == b"hello bytes"

    async with async_session_maker() as session:
        new_org, counts = await portability.import_org(
            session, payload, slug="arch-dst", files=blobs
        )
        await session.commit()
        new_org_id = new_org.id
    assert counts["files"] == 1

    async with async_session_maker() as session:
        await set_current_org(session, new_org_id)
        rows = (
            (await session.execute(select(StoredFile).where(StoredFile.org_id == new_org_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        restored = rows[0]
        assert restored.id != file_id
        # Re-keyed onto its own org, not left pointing into the source's prefix.
        assert restored.storage_key == f"{new_org_id}/{restored.id}"
        assert storage_for(restored.backend).open(restored.storage_key).read() == b"hello bytes"


async def test_archive_names_its_own_gaps(monkeypatch, tmp_path) -> None:
    """An unreadable blob (volume drift, a backend since removed) is recorded, not fatal: an
    archive that names its gaps is worth more than no archive at all."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    tenant = await make_tenant("arch-gap")
    file_id = await _seed_file(tenant.org.id, b"soon gone")
    # Drop the bytes but keep the row — exactly the drift the file router already 404s on.
    get_storage().delete(f"{tenant.org.id}/{file_id}")

    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        blob = await portability.build_archive(session, org)

    payload, blobs = portability.read_archive(blob)
    assert payload["files_missing"] == [str(file_id)]
    assert blobs == {}


async def test_import_without_bytes_still_rekeys(monkeypatch, tmp_path) -> None:
    """A rows-only import must not leave the new org reading the source org's blobs. The row
    reads as bytes-missing instead, which is honest and already a handled state."""
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    source = await make_tenant("arch-nb-src")
    await _seed_file(source.org.id, b"stays put")

    async with async_session_maker() as session:
        org = await session.get(Org, source.org.id)
        payload = await portability.export_org(session, org)

    async with async_session_maker() as session:
        new_org, _ = await portability.import_org(session, payload, slug="arch-nb-dst")
        await session.commit()
        new_org_id = new_org.id

    async with async_session_maker() as session:
        await set_current_org(session, new_org_id)
        restored = (
            (await session.execute(select(StoredFile).where(StoredFile.org_id == new_org_id)))
            .scalars()
            .all()
        )[0]
        assert restored.storage_key.startswith(f"{new_org_id}/")
        assert str(source.org.id) not in restored.storage_key
