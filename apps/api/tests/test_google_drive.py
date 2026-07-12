"""google.drive (#21): links CRUD + rollup, browse cache, provisioning outbox, unlink safety."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx

from app.core.crypto import encrypt
from app.core.events import SystemContext, emit
from app.db import async_session_maker, set_current_org
from app.modules.google.drive.models import DriveFolderJob, DriveLink
from app.modules.google.drive.service import provision_folder
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_DRIVE
from tests.conftest import auth_cookie, make_tenant

FOLDER_MIME = "application/vnd.google-apps.folder"


class _StubResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None, headers=None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _StubClient:
    def __init__(self, script: list[tuple[str, _StubResponse]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []

    async def _pop(self, method: str, url: str, **kwargs) -> _StubResponse:  # noqa: ARG002
        self.calls.append((method, url))
        assert self.script, f"unexpected Google call: {method} {url}"
        expected, response = self.script.pop(0)
        assert expected == method, f"expected {expected}, got {method} {url}"
        return response

    async def get(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("POST", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("DELETE", url, **kwargs)


def _stub_acting_as(stub: _StubClient):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value


async def _seed(tenant, *, auto_provision: bool = False, automation: bool = False):
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                drive_enabled=True,
                drive_shared_drive_id="sd-1",
                drive_parent_folder_id="parent-1",
                drive_auto_provision=auto_provision,
                automation_connection_user_id=tenant.user.id if automation else None,
            )
        )
        session.add(
            GoogleConnection(
                org_id=tenant.org.id,
                user_id=tenant.user.id,
                google_sub="sub",
                email="me@agency.nl",
                scopes=["openid", "email", SCOPE_DRIVE],
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()


async def test_links_crud_rollup_and_unlink_never_deletes(client_for, monkeypatch) -> None:
    t = await make_tenant("gdrive-links")
    await _seed(t)
    headers = await auth_cookie(t.user)

    file_meta = _StubResponse(
        200,
        {
            "id": "file-1",
            "name": "Offerte.pdf",
            "mimeType": "application/pdf",
            "webViewLink": "https://drive.google.com/file/d/file-1",
            "driveId": "sd-1",
        },
    )
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Review", "project_id": project["id"]},
                headers=headers,
            )
        ).json()

        monkeypatch.setattr(
            "app.modules.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", file_meta)])),
        )
        created = await c.post(
            "/api/v1/google/drive/links",
            json={
                "entity_type": "task",
                "entity_id": task["id"],
                "drive_file_id": "file-1",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        link = created.json()
        assert link["name"] == "Offerte.pdf" and link["is_folder"] is False

        # Roll-up: the task's file surfaces on its project (#21 prefers the roll-up).
        rolled = (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "project", "entity_id": project["id"], "rollup": True},
                headers=headers,
            )
        ).json()
        assert [item["drive_file_id"] for item in rolled] == ["file-1"]
        flat = (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "project", "entity_id": project["id"]},
                headers=headers,
            )
        ).json()
        assert flat == []

        # Unlink: 204, the reference is gone, and the empty stub script proves no Drive call.
        monkeypatch.setattr(
            "app.modules.google.drive.service.acting_as", _stub_acting_as(_StubClient([]))
        )
        assert (
            await c.delete(f"/api/v1/google/drive/links/{link['id']}", headers=headers)
        ).status_code == 204
        assert (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "task", "entity_id": task["id"]},
                headers=headers,
            )
        ).json() == []


async def test_browse_caches_and_refresh_busts(client_for, monkeypatch) -> None:
    t = await make_tenant("gdrive-browse")
    await _seed(t)
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.modules.google.drive.service.get_redis", lambda: fake_redis)

    listing = _StubResponse(
        200,
        {
            "files": [
                {"id": "sub-1", "name": "Contracten", "mimeType": FOLDER_MIME},
                {
                    "id": "f-2",
                    "name": "Logo.png",
                    "mimeType": "image/png",
                    "size": "1024",
                    "webViewLink": "https://drive.google.com/file/d/f-2",
                },
            ]
        },
    )
    folder_meta = _StubResponse(200, {"id": "parent-1", "name": "Klanten"})
    stub = _StubClient([("GET", listing), ("GET", folder_meta)])
    monkeypatch.setattr("app.modules.google.drive.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        first = (await c.get("/api/v1/google/drive/browse", headers=headers)).json()
        assert first["folder"]["name"] == "Klanten"
        assert [item["name"] for item in first["items"]] == ["Contracten", "Logo.png"]
        assert first["items"][0]["is_folder"] is True

        # Second read comes from the cache — the exhausted stub proves no second Google call.
        second = (await c.get("/api/v1/google/drive/browse", headers=headers)).json()
        assert second == first

        # refresh=1 busts the cache: a new scripted round-trip is consumed.
        stub2 = _StubClient([("GET", listing), ("GET", folder_meta)])
        monkeypatch.setattr(
            "app.modules.google.drive.service.acting_as", _stub_acting_as(stub2)
        )
        refreshed = await c.get(
            "/api/v1/google/drive/browse", params={"refresh": True}, headers=headers
        )
        assert refreshed.status_code == 200 and stub2.script == []


async def test_company_created_queues_folder_and_worker_provisions(monkeypatch) -> None:
    t = await make_tenant("gdrive-prov")
    await _seed(t, auto_provision=True, automation=True)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    company_id = uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit(
            "company.created",
            ctx,
            {"company_id": company_id, "status": "active", "title": "Nieuwe Klant BV",
             "_recipients": []},
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        job = (await session.execute(select(DriveFolderJob))).scalar_one()
        assert job.status == "pending" and job.name == "Nieuwe Klant BV"

        # Worker: no name match under the parent → create → link stored, job done.
        stub = _StubClient(
            [
                ("GET", _StubResponse(200, {"files": []})),
                (
                    "POST",
                    _StubResponse(
                        200,
                        {
                            "id": "folder-9",
                            "name": "Nieuwe Klant BV",
                            "webViewLink": "https://drive.google.com/drive/folders/folder-9",
                        },
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.modules.google.drive.service.acting_as", _stub_acting_as(stub)
        )
        await provision_folder(session, t.org, job)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        link = (await session.execute(select(DriveLink))).scalar_one()
        assert link.entity_type == "company" and link.entity_id == company_id
        assert link.drive_file_id == "folder-9" and link.is_folder is True
        job = (await session.execute(select(DriveFolderJob))).scalar_one()
        assert job.status == "done"


async def test_provisioning_links_existing_folder_instead_of_duplicating(monkeypatch) -> None:
    t = await make_tenant("gdrive-match")
    await _seed(t, auto_provision=True, automation=True)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = DriveFolderJob(
            org_id=t.org.id,
            entity_type="company",
            entity_id=uuid.uuid4(),
            name="Bestaande Klant",
        )
        session.add(job)
        await session.flush()
        # The name-match hit means NO create call — the script only offers the search.
        stub = _StubClient(
            [
                (
                    "GET",
                    _StubResponse(
                        200,
                        {
                            "files": [
                                {
                                    "id": "existing-7",
                                    "name": "Bestaande Klant",
                                    "webViewLink": "https://drive/x",
                                }
                            ]
                        },
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.modules.google.drive.service.acting_as", _stub_acting_as(stub)
        )
        await provision_folder(session, t.org, job)
        assert job.status == "done" and stub.script == []
        from sqlalchemy import select

        link = (await session.execute(select(DriveLink))).scalar_one()
        assert link.drive_file_id == "existing-7"
        await session.commit()


async def test_bulk_provision_queues_only_folderless_companies(client_for, monkeypatch) -> None:
    t = await make_tenant("gdrive-bulk")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with client_for(t.host) as c:
        with_folder = (
            await c.post("/api/v1/companies", json={"name": "Heeft map"}, headers=headers)
        ).json()
        await c.post("/api/v1/companies", json={"name": "Zonder map"}, headers=headers)
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            session.add(
                DriveLink(
                    org_id=t.org.id,
                    entity_type="company",
                    entity_id=uuid.UUID(with_folder["id"]),
                    drive_file_id="f",
                    drive_url="https://drive/x",
                    name="Heeft map",
                    is_folder=True,
                )
            )
            await session.commit()

        result = await c.post("/api/v1/google/drive/provision-all", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["queued"] == 1


async def test_drive_links_tenant_isolation(client_for) -> None:
    a = await make_tenant("gdrive-iso-a")
    b = await make_tenant("gdrive-iso-b")
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        session.add(
            DriveLink(
                org_id=a.org.id,
                entity_type="company",
                entity_id=uuid.uuid4(),
                drive_file_id="f",
                drive_url="https://drive/x",
                name="Geheim",
            )
        )
        await session.commit()
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        assert (
            await cb.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "company", "entity_id": str(uuid.uuid4())},
                headers=b_headers,
            )
        ).json() == []
