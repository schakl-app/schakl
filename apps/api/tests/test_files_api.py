"""Pluggable file storage (#123): upload round-trip, guardrails, tenant isolation."""

from __future__ import annotations

from app.config import settings
from tests.conftest import auth_cookie, make_tenant

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


async def test_upload_and_serve_round_trip(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-rt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/files",
            files={"file": ("logo.png", _PNG, "image/png")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        meta = created.json()
        assert meta["filename"] == "logo.png"
        assert meta["size_bytes"] == len(_PNG)

        served = await c.get(f"/api/v1/files/{meta['id']}", headers=headers)
        assert served.status_code == 200
        assert served.content == _PNG
        assert served.headers["content-type"].startswith("image/png")
        assert "nosniff" in served.headers["x-content-type-options"]
        etag = served.headers["etag"]

        # A repeat fetch with the ETag costs a 304, no bytes.
        cached = await c.get(
            f"/api/v1/files/{meta['id']}",
            headers={**headers, "If-None-Match": etag},
        )
        assert cached.status_code == 304


async def test_upload_guardrails(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-guard")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A content type outside the allow-list is refused.
        refused = await c.post(
            "/api/v1/files",
            files={"file": ("evil.exe", b"MZ...", "application/x-msdownload")},
            headers=headers,
        )
        assert refused.status_code == 422

        # Oversized uploads are refused by the *measured* size, not the client's claim.
        monkeypatch.setattr(settings, "upload_max_bytes", 16)
        too_big = await c.post(
            "/api/v1/files",
            files={"file": ("big.png", _PNG, "image/png")},
            headers=headers,
        )
        assert too_big.status_code == 413

        # SVG uploads are accepted but served as a download, never inline (stored-XSS guard).
        monkeypatch.setattr(settings, "upload_max_bytes", 10 * 1024 * 1024)
        svg = await c.post(
            "/api/v1/files",
            files={"file": ("pic.svg", b"<svg/>", "image/svg+xml")},
            headers=headers,
        )
        assert svg.status_code == 201
        served = await c.get(f"/api/v1/files/{svg.json()['id']}", headers=headers)
        assert served.headers["content-disposition"].startswith("attachment")


async def test_files_tenant_isolation(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    a = await make_tenant("files-iso-a")
    b = await make_tenant("files-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        file_id = (
            await ca.post(
                "/api/v1/files",
                files={"file": ("a.png", _PNG, "image/png")},
                headers=a_headers,
            )
        ).json()["id"]
    async with client_for(b.host) as cb:
        # Another tenant's id reads as absent, never as forbidden.
        assert (await cb.get(f"/api/v1/files/{file_id}", headers=b_headers)).status_code == 404


async def test_avatar_override_and_effective_url(client_for) -> None:
    """#122: PATCH /meta/me sets/clears the personal override; the lookup carries the
    effective avatar for every picker in one query."""
    t = await make_tenant("avatar-me")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        me = (await c.get("/api/v1/meta/me", headers=headers)).json()
        assert me["avatar_url"] is None

        saved = await c.patch(
            "/api/v1/meta/me",
            json={"custom_avatar_url": "/api/v1/files/00000000-0000-0000-0000-000000000001"},
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["avatar_url"].endswith("0001")
        assert saved.json()["custom_avatar_url"] == saved.json()["avatar_url"]

        lookup = (await c.get("/api/v1/members/lookup", headers=headers)).json()
        assert lookup[0]["avatar_url"].endswith("0001")

        # Empty clears back to the OIDC picture / initials.
        cleared = await c.patch(
            "/api/v1/meta/me", json={"custom_avatar_url": ""}, headers=headers
        )
        assert cleared.json()["avatar_url"] is None
        assert cleared.json()["custom_avatar_url"] is None


async def test_branding_upload_gate_and_public_serve(client_for, tmp_path, monkeypatch) -> None:
    """Branding files serve anonymously (the login screen shows them), so tagging an upload
    as branding requires the branding permission — a plain member cannot publish public files."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-brand")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        up = await c.post(
            "/api/v1/files?entity_type=branding",
            files={"file": ("logo.png", _PNG, "image/png")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        file_id = up.json()["id"]

        # Anonymous fetch of the public URL succeeds — no cookie at all.
        anon = await c.get(f"/api/v1/files/{file_id}/public")
        assert anon.status_code == 200
        assert anon.content == _PNG
        assert anon.headers["cache-control"].startswith("public")

        # The private route still requires a session.
        assert (await c.get(f"/api/v1/files/{file_id}")).status_code == 401

        # A non-branding file is NOT reachable through the public route.
        plain = await c.post(
            "/api/v1/files",
            files={"file": ("doc.png", _PNG, "image/png")},
            headers=headers,
        )
        assert (await c.get(f"/api/v1/files/{plain.json()['id']}/public")).status_code == 404

        # Branding uploads are images only.
        pdf = await c.post(
            "/api/v1/files?entity_type=branding",
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
        assert pdf.status_code == 422

    member = await make_tenant("files-brand-m", role="member")
    m_headers = await auth_cookie(member.user)
    async with client_for(member.host) as c:
        refused = await c.post(
            "/api/v1/files?entity_type=branding",
            files={"file": ("logo.png", _PNG, "image/png")},
            headers=m_headers,
        )
        assert refused.status_code == 403


async def test_public_serve_is_tenant_scoped(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    a = await make_tenant("files-pub-a")
    b = await make_tenant("files-pub-b")
    a_headers = await auth_cookie(a.user)
    async with client_for(a.host) as ca:
        file_id = (
            await ca.post(
                "/api/v1/files?entity_type=branding",
                files={"file": ("logo.png", _PNG, "image/png")},
                headers=a_headers,
            )
        ).json()["id"]
        assert (await ca.get(f"/api/v1/files/{file_id}/public")).status_code == 200
    async with client_for(b.host) as cb:
        # Another tenant's branding id reads as absent on the public route too.
        assert (await cb.get(f"/api/v1/files/{file_id}/public")).status_code == 404


async def test_attachments_list_delete_and_task_activity(client_for, tmp_path, monkeypatch) -> None:
    """#123 follow-up: files attach to a task, list per entity, delete removes bytes+row,
    and the task's activity trail records both sides."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-att")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "Brief"}, headers=headers)).json()

        up = await c.post(
            f"/api/v1/files?entity_type=task&entity_id={task['id']}",
            files={"file": ("brief.pdf", b"%PDF-1.4 brief", "application/pdf")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        file_id = up.json()["id"]

        listed = (
            await c.get(
                f"/api/v1/files?entity_type=task&entity_id={task['id']}", headers=headers
            )
        ).json()
        assert [f["id"] for f in listed] == [file_id]

        # Attaching to a task that does not exist fails the upload (handler validates).
        missing = await c.post(
            "/api/v1/files?entity_type=task&entity_id=00000000-0000-0000-0000-00000000dead",
            files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
        assert missing.status_code == 404

        deleted = await c.delete(f"/api/v1/files/{file_id}", headers=headers)
        assert deleted.status_code == 204
        assert (await c.get(f"/api/v1/files/{file_id}", headers=headers)).status_code == 404

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        actions = [a["action"] for a in detail.get("activities", [])]
        assert "attachment_added" in actions
        assert "attachment_deleted" in actions


async def test_avatar_file_delete_is_personal(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-av-own")
    other = await make_tenant("files-av-two")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        up = await c.post(
            "/api/v1/files?entity_type=avatar",
            files={"file": ("me.png", _PNG, "image/png")},
            headers=headers,
        )
        file_id = up.json()["id"]

        # Another member of the same org may not delete someone's avatar.
        from app.db import async_session_maker, set_current_org
        from tests.conftest import add_membership

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await add_membership(session, t.org.id, other.user.id, "admin")
            await session.commit()
        other_headers = await auth_cookie(other.user)
        refused = await c.delete(f"/api/v1/files/{file_id}", headers=other_headers)
        assert refused.status_code == 403

        # The owner may.
        assert (await c.delete(f"/api/v1/files/{file_id}", headers=headers)).status_code == 204


async def test_app_icon_size_variants(client_for, tmp_path, monkeypatch) -> None:
    """The public branding serve derives real PNG size variants for the PWA icon story
    (#198): square-cropped, resized, and — maskable — padded into the safe zone."""
    import io

    from PIL import Image

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-icon")
    headers = await auth_cookie(t.user)

    # A non-square source proves the centre crop: 640x480, all-brand pixels.
    buf = io.BytesIO()
    Image.new("RGBA", (640, 480), "#4f46e5").save(buf, "PNG")
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/files?entity_type=branding",
            files={"file": ("icon.png", buf.getvalue(), "image/png")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        file_id = created.json()["id"]

        for size in (180, 192, 512):
            res = await c.get(f"/api/v1/files/{file_id}/public?size={size}")
            assert res.status_code == 200
            assert res.headers["content-type"] == "image/png"
            img = Image.open(io.BytesIO(res.content))
            assert img.size == (size, size)

        # Maskable pads the artwork on an opaque background; still exactly the asked size.
        res = await c.get(f"/api/v1/files/{file_id}/public?size=192&maskable=true")
        assert res.status_code == 200
        img = Image.open(io.BytesIO(res.content))
        assert img.size == (192, 192)
        # The pad ring is the bg colour (default white), the centre stays the icon's colour.
        assert img.convert("RGB").getpixel((2, 2)) == (255, 255, 255)
        centre = img.convert("RGB").getpixel((96, 96))
        assert centre != (255, 255, 255)

        # A variant answers 304 on its own ETag — and a different size has a different one.
        etag = res.headers["etag"]
        again = await c.get(
            f"/api/v1/files/{file_id}/public?size=192&maskable=true",
            headers={"If-None-Match": etag},
        )
        assert again.status_code == 304
        other = await c.get(f"/api/v1/files/{file_id}/public?size=512")
        assert other.headers["etag"] != etag

        # An unknown size serves the original bytes unchanged (no open resize proxy).
        res = await c.get(f"/api/v1/files/{file_id}/public?size=333")
        assert res.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(res.content)).size == (640, 480)


async def test_app_icon_url_branding_round_trip(client_for) -> None:
    """`app_icon_url` (#198) rides org branding: writable via PATCH, readable pre-auth
    (the manifest needs it), clearable with an empty string, and tenant-scoped."""
    t = await make_tenant("branding-appicon")
    other = await make_tenant("branding-appicon-other")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        updated = await c.patch(
            "/api/v1/meta/tenant",
            json={"app_icon_url": "/api/v1/files/00000000-0000-0000-0000-000000000001/public"},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["app_icon_url"] is not None

        # Pre-auth read — the login screen / manifest fetches this without a session.
        anon = await c.get("/api/v1/meta/tenant")
        assert anon.json()["app_icon_url"] is not None

        cleared = await c.patch("/api/v1/meta/tenant", json={"app_icon_url": ""}, headers=headers)
        assert cleared.json()["app_icon_url"] is None

    async with client_for(other.host) as c:
        assert (await c.get("/api/v1/meta/tenant")).json()["app_icon_url"] is None


class _FakeS3:
    """In-memory stand-in for S3ObjectStorage (#190) — the protocol, no network."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, key: str, stream) -> None:
        self.blobs[key] = stream.read()

    def open(self, key: str):
        import io

        if key not in self.blobs:
            raise FileNotFoundError(key)
        return io.BytesIO(self.blobs[key])

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


def _enable_fake_s3(monkeypatch) -> _FakeS3:
    """Point the instance at S3 (#190) the way env vars would, faked at the client seam."""
    from app.core.storage import backend as backend_mod

    fake = _FakeS3()
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "storage_s3_endpoint", "http://minio.local:9000")
    monkeypatch.setattr(settings, "storage_s3_bucket", "schakl")
    monkeypatch.setattr(settings, "storage_s3_access_key_id", "key")
    monkeypatch.setattr(settings, "storage_s3_secret_access_key", "secret")
    original = backend_mod.storage_for

    def fake_storage_for(name: str):
        if name == "s3":
            from app.core.storage.s3 import s3_configured

            if not s3_configured():
                raise backend_mod.StorageUnavailableError(name)
            return fake
        return original(name)

    # Patch every import site of the seam (service + router bind the name at import).
    import app.core.storage.router as router_mod
    import app.core.storage.service as service_mod

    monkeypatch.setattr(backend_mod, "storage_for", fake_storage_for)
    monkeypatch.setattr(service_mod, "storage_for", fake_storage_for)
    monkeypatch.setattr(router_mod, "storage_for", fake_storage_for)
    monkeypatch.setattr(
        service_mod, "get_storage", lambda: fake_storage_for(settings.storage_backend)
    )
    return fake


async def test_s3_backend_records_and_serves_new_writes(
    client_for, tmp_path, monkeypatch
) -> None:
    """With SCHAKL_STORAGE_BACKEND=s3 (#190), new uploads record backend="s3", land in the
    bucket, and serve back through the API — the bucket is never exposed directly."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-s3")
    headers = await auth_cookie(t.user)

    # A pre-existing local row (written before S3 was enabled).
    async with client_for(t.host) as c:
        local_meta = (
            await c.post(
                "/api/v1/files",
                files={"file": ("old.png", _PNG, "image/png")},
                headers=headers,
            )
        ).json()
        assert local_meta["backend"] == "local"

    fake = _enable_fake_s3(monkeypatch)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/files",
            files={"file": ("new.png", _PNG, "image/png")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        meta = created.json()
        assert meta["backend"] == "s3"
        # The bytes went to the bucket, under the org-prefixed key.
        assert fake.blobs[meta["storage_key"]] == _PNG
        assert meta["storage_key"].startswith(str(t.org.id))

        # Reads dispatch on the row's backend: the s3 row serves from the fake bucket…
        served = await c.get(f"/api/v1/files/{meta['id']}", headers=headers)
        assert served.status_code == 200 and served.content == _PNG
        # …and the pre-S3 local row still serves from the volume (override, not migration).
        old = await c.get(f"/api/v1/files/{local_meta['id']}", headers=headers)
        assert old.status_code == 200 and old.content == _PNG

        # Deleting the s3 row removes the object from the bucket.
        assert (
            await c.delete(f"/api/v1/files/{meta['id']}", headers=headers)
        ).status_code == 204
        assert meta["storage_key"] not in fake.blobs


async def test_s3_row_with_config_removed_reads_as_distinct_404(
    client_for, tmp_path, monkeypatch
) -> None:
    """An `s3` row whose instance config was since removed answers the distinct
    errors.storage_backend_unavailable — an ops pointer, not a generic bad link."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-s3-gone")
    headers = await auth_cookie(t.user)

    fake = _enable_fake_s3(monkeypatch)
    async with client_for(t.host) as c:
        meta = (
            await c.post(
                "/api/v1/files",
                files={"file": ("f.png", _PNG, "image/png")},
                headers=headers,
            )
        ).json()
        assert meta["backend"] == "s3"

    # The operator unsets the S3 env config; the instance falls back to local for writes.
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_s3_endpoint", "")
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/files/{meta['id']}", headers=headers)
        assert res.status_code == 404
        assert res.json()["error"]["message"] == "errors.storage_backend_unavailable"

        # Deleting the row still works — the blob is orphaned space, not a locked tenant.
        assert (
            await c.delete(f"/api/v1/files/{meta['id']}", headers=headers)
        ).status_code == 204
    assert fake.blobs  # the orphaned object is still in the (fake) bucket
