"""Images on tasks, clients and projects (the image-attachment research task).

Four things the storage core learned: a JSON (base64) upload route for the callers a
multipart body shuts out — the generated MCP tools and every JSON-only automation; a
thumbnail route so an attachment strip shows the screenshot rather than its filename; a
per-file ``client_visible`` bit that decides what a client-portal login may read on a task,
project or client (the API had left that to the web); and documents on the company hub.
"""

from __future__ import annotations

import base64
import io

from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


def _png(width: int = 64, height: int = 32, *, alpha: bool = False) -> bytes:
    from PIL import Image

    img = Image.new("RGBA" if alpha else "RGB", (width, height), (200, 30, 30, 128))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _jpeg(width: int = 1600, height: int = 900) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (10, 120, 200))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


async def test_inline_upload_is_the_multipart_upload_in_json(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-inline")
    headers = await auth_cookie(t.user)
    png = _png()
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"due_date": FAR_FUTURE_DUE, "title": "Screenshot"},
                headers=headers,
            )
        ).json()
        created = await c.post(
            "/api/v1/files/inline",
            json={
                "filename": "shot.png",
                "content_type": "image/png",
                # A data: URL is what a browser hands you for a pasted image — accepted as-is.
                "data": "data:image/png;base64," + base64.b64encode(png).decode(),
                "entity_type": "task",
                "entity_id": task["id"],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        meta = created.json()
        assert meta["size_bytes"] == len(png)
        assert meta["client_visible"] is False

        served = await c.get(f"/api/v1/files/{meta['id']}", headers=headers)
        assert served.status_code == 200
        assert served.content == png

        # Same guardrails as the multipart route: type allow-list …
        refused = await c.post(
            "/api/v1/files/inline",
            json={
                "filename": "x.exe",
                "content_type": "application/x-msdownload",
                "data": base64.b64encode(b"MZ").decode(),
            },
            headers=headers,
        )
        assert refused.status_code == 422
        # … the size ceiling, refused on the *encoded* length before any decode …
        monkeypatch.setattr(settings, "upload_max_bytes", 16)
        too_big = await c.post(
            "/api/v1/files/inline",
            json={
                "filename": "big.png",
                "content_type": "image/png",
                "data": base64.b64encode(png).decode(),
            },
            headers=headers,
        )
        assert too_big.status_code == 413
        monkeypatch.setattr(settings, "upload_max_bytes", 10 * 1024 * 1024)
        # … and bytes that are not base64 at all name the field.
        garbage = await c.post(
            "/api/v1/files/inline",
            json={"filename": "a.png", "content_type": "image/png", "data": "***not-b64***"},
            headers=headers,
        )
        assert garbage.status_code == 422
        assert garbage.json()["error"]["fields"] == {"data": "errors.invalid_base64"}

        # The task's trail recorded the attachment, exactly as a multipart upload does.
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert any(a["action"] == "attachment_added" for a in detail["activities"])


async def test_thumbnail_scales_rasters_and_keeps_alpha(
    client_for, tmp_path, monkeypatch
) -> None:
    from PIL import Image

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-thumb")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        photo = await c.post(
            "/api/v1/files",
            files={"file": ("photo.jpg", _jpeg(), "image/jpeg")},
            headers=headers,
        )
        assert photo.status_code == 201, photo.text
        thumb = await c.get(
            f"/api/v1/files/{photo.json()['id']}/thumbnail?size=160", headers=headers
        )
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(thumb.content)) as img:
            # Long edge 160, aspect kept (1600x900 → 160x90).
            assert img.size == (160, 90)
        # Cached by ETag like the original: a repeat fetch costs a 304 and no resize.
        again = await c.get(
            f"/api/v1/files/{photo.json()['id']}/thumbnail?size=160",
            headers={**headers, "If-None-Match": thumb.headers["etag"]},
        )
        assert again.status_code == 304

        # A transparent PNG stays PNG (a JPEG would paint the background black) and a source
        # smaller than the requested size is not blown up.
        icon = await c.post(
            "/api/v1/files",
            files={"file": ("icon.png", _png(64, 32, alpha=True), "image/png")},
            headers=headers,
        )
        thumb = await c.get(
            f"/api/v1/files/{icon.json()['id']}/thumbnail?size=480", headers=headers
        )
        assert thumb.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(thumb.content)) as img:
            assert img.size == (64, 32)
            assert img.mode == "RGBA"

        # An unknown size snaps to the nearest served one rather than 422-ing an <img>.
        odd = await c.get(
            f"/api/v1/files/{photo.json()['id']}/thumbnail?size=200", headers=headers
        )
        assert odd.status_code == 200
        assert odd.headers["etag"] == thumb.headers["etag"].replace(
            icon.json()["id"], photo.json()["id"]
        ).replace("t480", "t160")

        # A non-raster (a PDF) answers the original bytes: the <img> is wrong, not the API.
        pdf = await c.post(
            "/api/v1/files",
            files={"file": ("brief.pdf", b"%PDF-1.4 brief", "application/pdf")},
            headers=headers,
        )
        raw = await c.get(f"/api/v1/files/{pdf.json()['id']}/thumbnail", headers=headers)
        assert raw.status_code == 200
        assert raw.content == b"%PDF-1.4 brief"


async def test_client_portal_reads_only_files_ticked_visible(
    client_for, tmp_path, monkeypatch
) -> None:
    """The task page hid attachments from a portal login with ``!isPortal``; the API served
    them to anyone who could see the task. One bit on the file decides now, on every path:
    the list, the bytes, and the thumbnail."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-portal")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-files@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Zichtbaar",
                    "company_id": company["id"],
                    "visible_to_client": True,
                },
                headers=headers,
            )
        ).json()
        hidden = (
            await c.post(
                f"/api/v1/files?entity_type=task&entity_id={task['id']}",
                files={"file": ("intern.png", _png(), "image/png")},
                headers=headers,
            )
        ).json()
        shown = (
            await c.post(
                "/api/v1/files/inline",
                json={
                    "filename": "voor-klant.png",
                    "content_type": "image/png",
                    "data": base64.b64encode(_png(10, 10)).decode(),
                    "entity_type": "task",
                    "entity_id": task["id"],
                    "client_visible": True,
                },
                headers=headers,
            )
        ).json()
        assert shown["client_visible"] is True

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal = await auth_cookie(portal_user)

        listed = (
            await c.get(
                f"/api/v1/files?entity_type=task&entity_id={task['id']}", headers=portal
            )
        ).json()
        assert [f["id"] for f in listed] == [shown["id"]]
        assert (await c.get(f"/api/v1/files/{shown['id']}", headers=portal)).status_code == 200
        assert (await c.get(f"/api/v1/files/{hidden['id']}", headers=portal)).status_code == 404
        assert (
            await c.get(f"/api/v1/files/{hidden['id']}/thumbnail", headers=portal)
        ).status_code == 404
        # Staff still read both — the bit narrows the client, never the agency.
        assert (await c.get(f"/api/v1/files/{hidden['id']}", headers=headers)).status_code == 200

        # Flipping the bit is a write on the file, gated on files.file.write — a client holds
        # none of it — and recorded on the task's trail.
        refused = await c.patch(
            f"/api/v1/files/{hidden['id']}", json={"client_visible": True}, headers=portal
        )
        assert refused.status_code == 403
        flipped = await c.patch(
            f"/api/v1/files/{hidden['id']}", json={"client_visible": True}, headers=headers
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["client_visible"] is True
        assert (await c.get(f"/api/v1/files/{hidden['id']}", headers=portal)).status_code == 200
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert any(
            a["action"] == "attachment_visibility_changed" and a["payload"]["client_visible"]
            for a in detail["activities"]
        )


async def test_a_body_image_follows_the_words_not_the_eye(
    client_for, tmp_path, monkeypatch
) -> None:
    """An image pasted into a description or a comment is stored as *body* content
    (``inline=true`` → ``content_id``, the e-mail ``cid:`` shape): it never doubles up in the
    attachment strip, and a portal login reads it exactly when it reads the record that embeds
    it — the per-file eye gates attachments, never the words."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-body")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Body",
                    "last_name": "Klant",
                    "email": "body-files@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        visible = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Zichtbaar",
                    "company_id": company["id"],
                    "visible_to_client": True,
                },
                headers=headers,
            )
        ).json()
        internal = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Intern",
                    "company_id": company["id"],
                },
                headers=headers,
            )
        ).json()

        # Both upload envelopes can say "body content", and both mean the same column.
        body_img = (
            await c.post(
                f"/api/v1/files?entity_type=task&entity_id={visible['id']}&inline=true",
                files={"file": ("plaatje.png", _png(), "image/png")},
                headers=headers,
            )
        ).json()
        assert body_img["content_id"] == "body"
        internal_img = (
            await c.post(
                "/api/v1/files/inline",
                json={
                    "filename": "intern.png",
                    "content_type": "image/png",
                    "data": base64.b64encode(_png(10, 10)).decode(),
                    "entity_type": "task",
                    "entity_id": internal["id"],
                    "inline": True,
                },
                headers=headers,
            )
        ).json()
        assert internal_img["content_id"] == "body"

        # Body content is not an attachment: the strip's default list leaves it out.
        listed = (
            await c.get(
                f"/api/v1/files?entity_type=task&entity_id={visible['id']}", headers=headers
            )
        ).json()
        assert body_img["id"] not in [f["id"] for f in listed]

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal = await auth_cookie(portal_user)

        # The words of a client-visible task carry their images — bytes and thumbnail alike —
        # with no eye ever ticked; an internal task's body image stays a 404.
        assert (await c.get(f"/api/v1/files/{body_img['id']}", headers=portal)).status_code == 200
        assert (
            await c.get(f"/api/v1/files/{body_img['id']}/thumbnail", headers=portal)
        ).status_code == 200
        assert (
            await c.get(f"/api/v1/files/{internal_img['id']}", headers=portal)
        ).status_code == 404
        assert (
            await c.get(f"/api/v1/files/{internal_img['id']}/thumbnail", headers=portal)
        ).status_code == 404


async def test_documents_ride_the_company_hub(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-hub")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        ).json()
        docs = next(p for p in panels if p["key"] == "files.documents")
        assert docs["empty"] is True

        up = await c.post(
            f"/api/v1/files?entity_type=company&entity_id={company['id']}",
            files={"file": ("logo-proof.png", _png(), "image/png")},
            headers=headers,
        )
        assert up.status_code == 201, up.text
        panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        ).json()
        docs = next(p for p in panels if p["key"] == "files.documents")
        assert [f["filename"] for f in docs["data"]["items"]] == ["logo-proof.png"]

        # The client's trail says who pinned it (§16) …
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=company&entity_id={company['id']}",
                headers=headers,
            )
        ).json()
        assert any(row["action"] == "file_attached" for row in trail)
        # … and a client id that is not ours fails the upload rather than storing an orphan.
        missing = await c.post(
            "/api/v1/files?entity_type=company&entity_id=00000000-0000-0000-0000-00000000dead",
            files={"file": ("x.png", _png(), "image/png")},
            headers=headers,
        )
        assert missing.status_code == 404


def test_multipart_uploads_are_not_tools_and_the_json_upload_is() -> None:
    """A generated tool sends JSON, so a multipart route can only ever answer 422 through
    MCP (the 422 the task reported). Those routes are off the surface, by method, and the
    JSON twin is on it under its own name."""
    from app.core.mcp.server import _becomes_a_tool, _tool_index
    from app.main import app

    names, paths = _tool_index(app)
    assert names.get("upload_file_inline_api_v1_files_inline_post") == "upload_file_inline"
    assert paths["upload_file_inline"] == "/api/v1/files/inline"
    assert "upload_file" not in paths
    assert _becomes_a_tool("/api/v1/files", "GET")
    assert _becomes_a_tool("/api/v1/files/inline", "POST")
    for path in (
        "/api/v1/files",
        "/api/v1/hr/documents",
        "/api/v1/interactions/upload-eml",
        "/api/v1/companies/123/logo",
    ):
        assert not _becomes_a_tool(path, "POST"), path
