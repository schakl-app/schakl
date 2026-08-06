"""Content-addressed storage: identical bytes are stored once, per org (docs/STORAGE.md).

The saving is invisible in the JSON — two uploads of the same logo look exactly as they did
before — so every assertion here is about the *objects* and the ``file_blobs`` rows, and the
counting backend is what makes "no second upload" checkable at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.config import settings
from app.core.storage.jobs import fold_legacy_files, sweep_unreferenced
from app.core.storage.models import FileBlob, StoredFile
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
_OTHER = b"\x89PNG\r\n\x1a\n" + b"1" * 64


class _CountingStorage:
    """The storage protocol, in memory, counting what it was asked to write and remove."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.puts = 0
        self.deletes = 0

    def put(self, key: str, stream) -> None:
        self.puts += 1
        self.blobs[key] = stream.read()

    def open(self, key: str):
        import io

        if key not in self.blobs:
            raise FileNotFoundError(key)
        return io.BytesIO(self.blobs[key])

    def delete(self, key: str) -> None:
        self.deletes += 1
        self.blobs.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        marker = prefix.rstrip("/") + "/"
        doomed = [k for k in self.blobs if k.startswith(marker)]
        for key in doomed:
            del self.blobs[key]
        return len(doomed)


def _counting_backend(monkeypatch) -> _CountingStorage:
    """Swap the local backend for one that counts, at every site that binds the seam."""
    import app.core.storage.backend as backend_mod
    import app.core.storage.jobs as jobs_mod
    import app.core.storage.router as router_mod
    import app.core.storage.service as service_mod
    import app.core.storage.system as system_mod

    store = _CountingStorage()
    for module in (backend_mod, service_mod, router_mod, jobs_mod):
        monkeypatch.setattr(module, "storage_for", lambda _name, s=store: s, raising=False)
    for module in (service_mod, system_mod, backend_mod):
        monkeypatch.setattr(module, "get_storage", lambda s=store: s, raising=False)
    return store


async def _blobs(org_id: uuid.UUID) -> list[FileBlob]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            select(FileBlob).where(FileBlob.org_id == org_id).order_by(FileBlob.created_at)
        )
        return list(rows.scalars().all())


async def _file_count(org_id: uuid.UUID) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return int(
            await session.scalar(
                select(func.count()).select_from(StoredFile).where(StoredFile.org_id == org_id)
            )
            or 0
        )


async def test_identical_uploads_share_one_object(client_for, monkeypatch) -> None:
    """The same signature logo on two messages is two rows and one object — and, on object
    storage, one upload: the second write never reaches the backend at all."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-share")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        first = await c.post(
            "/api/v1/files", files={"file": ("logo.png", _PNG, "image/png")}, headers=headers
        )
        second = await c.post(
            "/api/v1/files",
            # A different filename on purpose: the *content* is the identity, not the name.
            files={"file": ("image001.png", _PNG, "image/png")},
            headers=headers,
        )
        assert first.status_code == 201 and second.status_code == 201
        one, two = first.json(), second.json()

        assert one["id"] != two["id"]
        assert one["storage_key"] == two["storage_key"]
        assert store.puts == 1, "the second upload must not travel to the backend"
        assert len(store.blobs) == 1
        assert len(await _blobs(t.org.id)) == 1

        # Both rows still serve their own bytes, with their own filenames.
        for meta, name in ((one, "logo.png"), (two, "image001.png")):
            served = await c.get(f"/api/v1/files/{meta['id']}", headers=headers)
            assert served.status_code == 200 and served.content == _PNG
            assert name in served.headers["content-disposition"]

        # Different content is a different blob; nothing collapses that shouldn't.
        third = await c.post(
            "/api/v1/files", files={"file": ("other.png", _OTHER, "image/png")}, headers=headers
        )
        assert third.status_code == 201
        assert store.puts == 2
        assert len(await _blobs(t.org.id)) == 2


async def test_deleting_one_shared_file_keeps_the_other_readable(client_for, monkeypatch) -> None:
    """The bug de-duplication could have introduced: one delete must not blank its siblings."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-delete")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        one = (
            await c.post(
                "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
            )
        ).json()
        two = (
            await c.post(
                "/api/v1/files", files={"file": ("b.png", _PNG, "image/png")}, headers=headers
            )
        ).json()

        assert (await c.delete(f"/api/v1/files/{one['id']}", headers=headers)).status_code == 204
        assert store.deletes == 0, "a shared object is never removed on the request path"
        still = await c.get(f"/api/v1/files/{two['id']}", headers=headers)
        assert still.status_code == 200 and still.content == _PNG


async def test_two_orgs_holding_the_same_bytes_never_share_an_object(
    client_for, monkeypatch
) -> None:
    """De-duplication stops at the tenant boundary (Golden Rule 1): each org's key space is
    its own, or terminating one would take the other's bytes with it."""
    store = _counting_backend(monkeypatch)
    one = await make_tenant("dedup-org-a")
    two = await make_tenant("dedup-org-b")

    keys = []
    for tenant in (one, two):
        headers = await auth_cookie(tenant.user)
        async with client_for(tenant.host) as c:
            meta = (
                await c.post(
                    "/api/v1/files",
                    files={"file": ("logo.png", _PNG, "image/png")},
                    headers=headers,
                )
            ).json()
        keys.append(meta["storage_key"])
        assert meta["storage_key"].startswith(str(tenant.org.id))

    assert keys[0] != keys[1]
    assert store.puts == 2
    assert len(await _blobs(one.org.id)) == 1
    assert len(await _blobs(two.org.id)) == 1


async def test_sweeper_reclaims_only_after_the_grace_window(client_for, monkeypatch) -> None:
    """Two passes: the first sighting stamps, the bytes go a grace window later. A blob is
    never collected in the same breath as the delete that unreferenced it."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-sweep")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        meta = (
            await c.post(
                "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
            )
        ).json()
        assert (await c.delete(f"/api/v1/files/{meta['id']}", headers=headers)).status_code == 204

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # First pass: nothing referenced it, so it is stamped — and kept.
        assert await sweep_unreferenced(session, t.org) == 0
        await session.commit()
    blob = (await _blobs(t.org.id))[0]
    assert blob.unreferenced_since is not None
    assert store.blobs, "the stamp pass must not delete anything"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Second pass, still inside the window: still kept.
        assert await sweep_unreferenced(session, t.org) == 0
        await session.commit()
    assert store.blobs

    # Age the stamp past the window and sweep again.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            update(FileBlob)
            .where(FileBlob.id == blob.id)
            .values(
                unreferenced_since=datetime.now(UTC)
                - timedelta(hours=settings.storage_blob_grace_hours + 1)
            )
        )
        assert await sweep_unreferenced(session, t.org) == 1
        await session.commit()
    assert store.blobs == {}
    assert await _blobs(t.org.id) == []


async def test_sweeper_leaves_a_blob_a_new_write_reclaimed(client_for, monkeypatch) -> None:
    """A blob whose content is uploaded again before the window closes is resurrected, not
    collected — the stamp is cleared by the write that reserved it."""
    _counting_backend(monkeypatch)
    t = await make_tenant("dedup-resurrect")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        meta = (
            await c.post(
                "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
            )
        ).json()
        await c.delete(f"/api/v1/files/{meta['id']}", headers=headers)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await sweep_unreferenced(session, t.org)
        await session.commit()
    stamped = (await _blobs(t.org.id))[0]
    assert stamped.unreferenced_since is not None

    async with client_for(t.host) as c:
        again = await c.post(
            "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
        )
        assert again.status_code == 201

    revived = (await _blobs(t.org.id))[0]
    assert revived.id == stamped.id
    assert revived.unreferenced_since is None

    # Even aged past the window, the sweep skips it: something references it again.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            update(FileBlob)
            .where(FileBlob.id == revived.id)
            .values(unreferenced_since=datetime.now(UTC) - timedelta(days=30))
        )
        assert await sweep_unreferenced(session, t.org) == 0
        await session.commit()

    async with client_for(t.host) as c:
        served = await c.get(f"/api/v1/files/{again.json()['id']}", headers=headers)
        assert served.status_code == 200


async def test_fold_collapses_pre_dedup_duplicates(client_for, monkeypatch) -> None:
    """The retroactive half: rows written before de-duplication existed are hashed in batches,
    the first donates its own object to the blob, and every later copy is freed."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-fold")
    headers = await auth_cookie(t.user)

    # The pre-de-duplication shape, written directly: every row its own object at its own key.
    ids = [uuid.uuid4() for _ in range(4)]
    contents = [_PNG, _PNG, _PNG, _OTHER]
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        for file_id, data in zip(ids, contents, strict=True):
            key = f"{t.org.id}/{file_id}"
            store.blobs[key] = data
            session.add(
                StoredFile(
                    id=file_id,
                    org_id=t.org.id,
                    backend="local",
                    storage_key=key,
                    filename=f"{file_id}.png",
                    content_type="image/png",
                    size_bytes=len(data),
                    created_by_user_id=t.user.id,
                )
            )
        await session.commit()
    assert len(store.blobs) == 4

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await fold_legacy_files(session, t.org) == 4
        await session.commit()

    # Three identical files now share one object, the odd one keeps its own: four rows, two
    # blobs, two objects. Nothing was copied — the blobs adopted the keys already there.
    assert await _file_count(t.org.id) == 4
    assert len(await _blobs(t.org.id)) == 2
    assert len(store.blobs) == 2
    assert store.puts == 0, "folding reads and repoints; it never rewrites an object"

    async with client_for(t.host) as c:
        for file_id, data in zip(ids, contents, strict=True):
            served = await c.get(f"/api/v1/files/{file_id}", headers=headers)
            assert served.status_code == 200 and served.content == data

    # Idempotent: a second run has nothing left to fold.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await fold_legacy_files(session, t.org) == 0


async def test_pre_dedup_row_still_deletes_its_own_bytes(client_for, monkeypatch) -> None:
    """Until the fold job reaches it, a legacy row owns its object outright and removing it
    must still free the space — the behaviour a rollback also relies on."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-legacy")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        meta = (
            await c.post(
                "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
            )
        ).json()

    legacy_key = f"{t.org.id}/{meta['id']}"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            update(StoredFile)
            .where(StoredFile.id == uuid.UUID(meta["id"]))
            .values(blob_id=None, storage_key=legacy_key)
        )
        await session.commit()
    store.blobs[legacy_key] = _PNG

    async with client_for(t.host) as c:
        assert (await c.delete(f"/api/v1/files/{meta['id']}", headers=headers)).status_code == 204
    assert legacy_key not in store.blobs


async def test_upload_costs_one_extra_statement(client_for, monkeypatch, count_queries) -> None:
    """De-duplication buys itself with exactly one statement: the reservation. A second upload
    of the same bytes costs the same query budget and no backend write (docs/PERFORMANCE.md)."""
    store = _counting_backend(monkeypatch)
    t = await make_tenant("dedup-budget")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/files", files={"file": ("a.png", _PNG, "image/png")}, headers=headers
        )
        with count_queries() as counter:
            again = await c.post(
                "/api/v1/files", files={"file": ("b.png", _PNG, "image/png")}, headers=headers
            )
        assert again.status_code == 201
    assert len(counter.matching("file_blobs")) == 1, counter.matching("file_blobs")
    assert store.puts == 1


async def test_a_renderer_reads_a_stored_image_back_by_its_own_storage_key(
    client_for, monkeypatch
) -> None:
    """The happy path of a loader whose failure branch is a shrug.

    ``load_org_image`` composed ``{org_id}/{id}`` — the layout *before* de-duplication. Since
    ``file_blobs`` the object lives at the blob's key, so the path it built had not existed for
    any file written since, and every branded document printed without its logo, its background
    mark and its report cover. Nothing caught it because the miss is an ``OSError`` and the
    function is deliberately forgiving: "branding must never be able to fail an invoice" is the
    right call, and it is exactly what makes a *positive* round-trip the only honest test.

    So this writes through the real upload route and reads back through the real loader. A
    mocked backend keyed on whatever the loader asked for would agree with itself and prove
    nothing; ``_CountingStorage`` only holds the key the *writer* used.
    """
    from app.core.branding import load_org_image
    from app.core.tenancy import RequestContext

    store = _counting_backend(monkeypatch)
    monkeypatch.setattr(
        "app.core.branding.storage_for", lambda _name, s=store: s, raising=False
    )
    t = await make_tenant("brandread")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/files",
            files={"file": ("cover.png", _PNG, "image/png")},
            headers=headers,
        )
    assert created.status_code == 201
    file_id = uuid.UUID(created.json()["id"])

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(type(t.org), t.org.id)
        ctx = RequestContext(user=t.user, org=org, session=session)
        payload, content_type = await load_org_image(ctx, file_id, what="report cover")

    assert payload == _PNG, "the renderer read nothing back — check the key it composed"
    assert content_type == "image/png"
