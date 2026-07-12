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
