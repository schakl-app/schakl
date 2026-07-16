"""Employee dossier (hr module): own-vs-any reads, employer uploads, sensitive serving."""

from __future__ import annotations

from app.config import settings
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_PDF = b"%PDF-1.4 fake"


async def _upload(
    c, headers, user_id: str, category: str = "contract", title: str = "Contract 2026"
):
    return await c.post(
        f"/api/v1/hr/documents?user_id={user_id}&category={category}&title={title}",
        files={"file": ("contract.pdf", _PDF, "application/pdf")},
        headers=headers,
    )


async def test_dossier_upload_read_and_own_scope(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("hr-own")
    member = await make_tenant("hr-own-m", email="member-hr@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    owner_h = await auth_cookie(t.user)
    member_h = await auth_cookie(member.user)

    async with client_for(t.host) as c:
        # The employer files a contract for the member.
        created = await _upload(c, owner_h, str(member.user.id))
        assert created.status_code == 201, created.text
        doc = created.json()
        assert doc["category"] == "contract"
        assert doc["uploaded_by_name"]

        # The member reads their own dossier and downloads their own document.
        dossier = (await c.get("/api/v1/hr/dossier", headers=member_h)).json()
        assert [d["id"] for d in dossier["documents"]] == [doc["id"]]
        served = await c.get(f"/api/v1/hr/documents/{doc['id']}/file", headers=member_h)
        assert served.status_code == 200 and served.content == _PDF

        # …but never a colleague's: the owner's dossier is empty, and asking for the
        # member's by id without :any answers 404 (existence must not leak).
        own_file = await _upload(c, owner_h, str(t.user.id), category="cao", title="CAO")
        other_doc = own_file.json()
        assert (
            await c.get(f"/api/v1/hr/documents/{other_doc['id']}/file", headers=member_h)
        ).status_code == 404
        assert (
            await c.get(f"/api/v1/hr/dossier?user_id={t.user.id}", headers=member_h)
        ).status_code == 403

        # A member cannot file documents (employer act), nor delete them.
        assert (await _upload(c, member_h, str(member.user.id))).status_code == 403
        assert (
            await c.delete(f"/api/v1/hr/documents/{doc['id']}", headers=member_h)
        ).status_code == 403

        # The generic file route answers the same 404 for someone else's dossier bytes.
        assert (
            await c.get(f"/api/v1/files/{other_doc['file_id']}", headers=member_h)
        ).status_code == 404

        # Managing roles read anyone's, delete cleans up row + file.
        listed = (
            await c.get(f"/api/v1/hr/dossier?user_id={member.user.id}", headers=owner_h)
        ).json()
        assert len(listed["documents"]) == 1
        assert (
            await c.delete(f"/api/v1/hr/documents/{doc['id']}", headers=owner_h)
        ).status_code == 204
        assert (
            await c.get(f"/api/v1/hr/documents/{doc['id']}/file", headers=owner_h)
        ).status_code == 404


async def test_dossier_validation_and_tenant_isolation(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("hr-iso")
    other = await make_tenant("hr-iso-other")
    owner_h = await auth_cookie(t.user)
    other_h = await auth_cookie(other.user)

    async with client_for(t.host) as c:
        # Unknown category refused; unknown user 404s.
        bad = await _upload(c, owner_h, str(t.user.id), category="nope")
        assert bad.status_code == 422
        ghost = await _upload(c, owner_h, "00000000-0000-0000-0000-000000000001")
        assert ghost.status_code == 404
        doc = (await _upload(c, owner_h, str(t.user.id))).json()

    async with client_for(other.host) as c:
        # Another tenant reaches nothing of it.
        assert (
            await c.get(f"/api/v1/hr/documents/{doc['id']}/file", headers=other_h)
        ).status_code == 404
        assert (
            await c.delete(f"/api/v1/hr/documents/{doc['id']}", headers=other_h)
        ).status_code == 404
