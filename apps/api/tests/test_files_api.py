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
        from tests.conftest import add_membership
        from app.db import async_session_maker, set_current_org

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await add_membership(session, t.org.id, other.user.id, "admin")
            await session.commit()
        other_headers = await auth_cookie(other.user)
        refused = await c.delete(f"/api/v1/files/{file_id}", headers=other_headers)
        assert refused.status_code == 403

        # The owner may.
        assert (await c.delete(f"/api/v1/files/{file_id}", headers=headers)).status_code == 204
