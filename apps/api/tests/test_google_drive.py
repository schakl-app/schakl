"""google.drive (#21): links CRUD + rollup, browse cache, provisioning outbox, unlink safety."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx

from app.core.auth.models import User
from app.core.crypto import encrypt
from app.core.events import SystemContext, emit
from app.db import async_session_maker, set_current_org
from app.integrations.google.drive.models import DriveFolderJob, DriveLink
from app.integrations.google.drive.service import provision_folder
from app.integrations.google.models import GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_DRIVE
from tests.conftest import FAR_FUTURE_DUE, add_membership, auth_cookie, make_tenant

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

    async def _pop(self, method: str, url: str, **kwargs) -> _StubResponse:
        self.calls.append((method, url))
        self.call_kwargs = getattr(self, "call_kwargs", [])
        self.call_kwargs.append(kwargs)
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

    async def patch(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("PATCH", url, **kwargs)


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

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def _seed(
    tenant,
    *,
    auto_provision: bool = False,
    automation: bool = False,
    parent_folder: str | None = "parent-1",
    shared_drive: str | None = "sd-1",
    drive_scope: bool = True,
):
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                drive_enabled=True,
                drive_shared_drive_id=shared_drive,
                drive_parent_folder_id=parent_folder,
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
                scopes=["openid", "email", *([SCOPE_DRIVE] if drive_scope else [])],
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
                json={"due_date": FAR_FUTURE_DUE, "title": "Review", "project_id": project["id"]},
                headers=headers,
            )
        ).json()

        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
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
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(_StubClient([]))
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
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: fake_redis)

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
    monkeypatch.setattr("app.integrations.google.drive.service.acting_as", _stub_acting_as(stub))

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
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub2)
        )
        refreshed = await c.get(
            "/api/v1/google/drive/browse", params={"refresh": True}, headers=headers
        )
        assert refreshed.status_code == 200 and stub2.script == []


async def test_browse_search_filters_at_drive_and_keys_its_own_cache(
    client_for, monkeypatch
) -> None:
    """#336: the filter is Drive's, the apostrophe is escaped, and the cache entry is its own.

    The listing is one page of 100 with no paging, so filtering in the browser would answer
    "nothing found" for a file that is merely 101st alphabetically. And the search entry may
    never be the folder's: sharing the key would serve one person's filtered list to the next
    person opening that folder, as its contents, for the length of the TTL.
    """
    t = await make_tenant("gdrive-search")
    await _seed(t)
    org_id, user_id = t.org.id, t.user.id
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: fake_redis)

    folder_meta = _StubResponse(200, {"id": "parent-1", "name": "Klanten"})
    hits = _StubResponse(200, {"files": [{"id": "f-9", "name": "O'Neill offerte.pdf"}]})
    stub = _StubClient([("GET", hits), ("GET", folder_meta)])
    monkeypatch.setattr("app.integrations.google.drive.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        searched = await c.get(
            "/api/v1/google/drive/browse", params={"q": "o'neill"}, headers=headers
        )
        assert searched.status_code == 200, searched.text
        body = searched.json()
        assert [item["name"] for item in body["items"]] == ["O'Neill offerte.pdf"]
        # Echoed, so the screen can name the term this list actually answers.
        assert body["query"] == "o'neill"
        # The escape is the point: a quote typed in a search box may not rewrite the query.
        assert stub.call_kwargs[0]["params"]["q"] == (
            "name contains 'o\\'neill' and 'parent-1' in parents and trashed=false"
        )

        # Same term again: served from the search's own cache entry, no second round-trip.
        assert (
            await c.get(
                "/api/v1/google/drive/browse", params={"q": "o'neill"}, headers=headers
            )
        ).json() == body

        # The *folder* is a different entry: a plain browse re-reads Drive and gets the
        # folder's real contents, not the filtered set.
        plain_files = _StubResponse(
            200,
            {
                "files": [
                    {"id": "f-1", "name": "Aanvraag.pdf"},
                    {"id": "f-9", "name": "O'Neill offerte.pdf"},
                ]
            },
        )
        stub2 = _StubClient([("GET", plain_files), ("GET", folder_meta)])
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(stub2),
        )
        plain = (await c.get("/api/v1/google/drive/browse", headers=headers)).json()
        assert [i["name"] for i in plain["items"]] == ["Aanvraag.pdf", "O'Neill offerte.pdf"]
        assert plain["query"] is None and stub2.script == []

    # Two distinct entries, and the folder's own one is the empty-term key.
    assert sorted(fake_redis.store) == [
        f"schakl:gdrive:browse:{org_id}:{user_id}:parent-1:",
        f"schakl:gdrive:browse:{org_id}:{user_id}:parent-1:o'neill",
    ]


async def test_browse_says_when_the_page_is_a_prefix_of_the_folder(
    client_for, monkeypatch
) -> None:
    """A truncated list that presents as complete is the §17 import failure, on a screen."""
    t = await make_tenant("gdrive-cap")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())
    folder_meta = _StubResponse(200, {"id": "parent-1", "name": "Klanten"})

    async with client_for(t.host) as c:
        capped = _StubResponse(
            200, {"files": [{"id": "f-1", "name": "A.pdf"}], "nextPageToken": "p2"}
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", capped), ("GET", folder_meta)])),
        )
        assert (await c.get("/api/v1/google/drive/browse", headers=headers)).json()["truncated"]

        whole = _StubResponse(200, {"files": [{"id": "f-1", "name": "A.pdf"}]})
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", whole), ("GET", folder_meta)])),
        )
        listing = (
            await c.get(
                "/api/v1/google/drive/browse", params={"refresh": True}, headers=headers
            )
        ).json()
        assert listing["truncated"] is False


def _google_error(status_code: int, reason: str | None, message: str) -> _StubResponse:
    """A Drive refusal shaped like Google's, so the reason survives to the response body."""
    body: dict = {"error": {"code": status_code, "message": message, "status": "PERMISSION_DENIED"}}
    if reason:
        body["error"]["details"] = [{"reason": reason}]
    response = _StubResponse(status_code, body)
    real = httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("GET", "https://www.googleapis.com/drive/v3/files"),
    )

    def _raise() -> None:
        raise httpx.HTTPStatusError("boom", request=real.request, response=real)

    response.raise_for_status = _raise  # type: ignore[method-assign]
    return response


async def test_browse_reports_googles_own_reason_not_a_500(client_for, monkeypatch) -> None:
    """A Drive 403 is three different problems; the picker must say which (#21 follow-up)."""
    t = await make_tenant("gdrive-403")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    cases = [
        (
            "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
            "Request had insufficient authentication scopes.",
            "errors.google_drive_scope_missing",
        ),
        (
            "SERVICE_DISABLED",
            "Google Drive API has not been used in project 123 before or it is disabled.",
            "errors.google_drive_api_disabled",
        ),
        (None, "The user does not have sufficient permissions for this file.",
         "errors.google_drive_forbidden"),
    ]
    async with client_for(t.host) as c:
        for reason, message, expected in cases:
            stub = _StubClient([("GET", _google_error(403, reason, message))])
            monkeypatch.setattr(
                "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
            )
            response = await c.get("/api/v1/google/drive/browse", headers=headers)
            # 409, never 500: every one of these is a state someone can fix.
            assert response.status_code == 409, response.text
            assert response.json()["error"]["message"] == expected


async def test_browse_refuses_a_connection_without_the_drive_scope(client_for) -> None:
    """No round-trip needed: the connection row already proves Drive was never consented to."""
    t = await make_tenant("gdrive-noscope")
    await _seed(t, drive_scope=False)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        response = await c.get("/api/v1/google/drive/browse", headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["message"] == "errors.google_drive_scope_missing"


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
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
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
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
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
                    # "Has a folder" is ``is_root``, not "some folder is linked here": a
                    # subfolder attached as a file must not make the client look provisioned.
                    is_root=True,
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


async def test_provisioning_falls_back_to_shared_drive_root(monkeypatch) -> None:
    """#149: no parent folder configured but a shared drive is — the worker parents the
    new folder on the shared drive's root instead of skipping as drive_not_configured."""
    t = await make_tenant("gdrive-sdroot")
    await _seed(t, automation=True, parent_folder=None)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from app.integrations.google.drive.service import queue_folder_job

        job = await queue_folder_job(session, t.org.id, "company", uuid.uuid4(), "Klant BV")
        stub = _StubClient(
            [
                ("GET", _StubResponse(200, {"files": []})),
                (
                    "POST",
                    _StubResponse(
                        200,
                        {"id": "f-1", "name": "Klant BV", "webViewLink": "https://drive/f-1"},
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
        )
        await provision_folder(session, t.org, job)
        await session.commit()

        assert job.status == "done" and job.last_error is None
        create_kwargs = stub.call_kwargs[-1]
        assert create_kwargs["json"]["parents"] == ["sd-1"]


async def test_auto_provision_queues_with_shared_drive_only(monkeypatch) -> None:
    """#260: automatic provisioning gated on ``drive_parent_folder_id`` alone, so an org with
    only a shared drive (#149's exact config) silently queued nothing on client/project create.
    ``_provisioning_on`` now uses ``drive_root`` like the manual button and backfill, so the
    ``company.created`` / ``project.created`` handlers queue a job that the worker roots on the
    shared drive."""
    t = await make_tenant("gdrive-auto-sdroot")
    await _seed(t, auto_provision=True, automation=True, parent_folder=None)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    company_id, project_id = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit(
            "company.created",
            ctx,
            {"company_id": company_id, "status": "active", "title": "Klant BV",
             "_recipients": []},
        )
        await emit(
            "project.created",
            ctx,
            {"project_id": project_id, "name": "Website", "company_id": company_id},
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        jobs = (await session.execute(select(DriveFolderJob))).scalars().all()
        by_type = {j.entity_type: j for j in jobs}
        assert by_type["company"].entity_id == company_id
        assert by_type["company"].status == "pending"
        assert by_type["project"].entity_id == project_id
        assert by_type["project"].parent_entity_id == company_id


async def test_auto_provision_skips_without_any_root(monkeypatch) -> None:
    """#260: the gate still closes when Drive is genuinely unconfigured — no parent folder and
    no shared drive means no job, not a job the worker can only skip."""
    t = await make_tenant("gdrive-auto-noroot")
    await _seed(t, auto_provision=True, automation=True, parent_folder=None, shared_drive=None)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit(
            "company.created",
            ctx,
            {"company_id": uuid.uuid4(), "status": "active", "title": "Klant BV",
             "_recipients": []},
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        assert (await session.execute(select(DriveFolderJob))).scalars().all() == []


async def test_provision_request_409s_without_any_root(client_for, monkeypatch) -> None:
    """#149: neither a parent folder nor a shared drive configured — the button must fail
    visibly with the existing error instead of accepting a job the worker can only skip."""
    t = await make_tenant("gdrive-noroot")
    await _seed(t, automation=True, parent_folder=None, shared_drive=None)
    headers = await auth_cookie(t.user)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        response = await c.post(
            "/api/v1/google/drive/provision",
            json={"entity_type": "company", "entity_id": company["id"]},
            headers=headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "google_drive_no_folder"


# --------------------------------------------------------------------------- #
# The folder picker: a record's folder is a stored decision, and re-pointing one
# is `google.drive.manage` while giving a record its first is not.
# --------------------------------------------------------------------------- #
def _folder_meta(file_id: str, name: str) -> _StubResponse:
    return _StubResponse(
        200,
        {
            "id": file_id,
            "name": name,
            "mimeType": FOLDER_MIME,
            "webViewLink": f"https://drive.google.com/drive/folders/{file_id}",
            "driveId": "sd-1",
        },
    )


async def _add_member_with_connection(org_id: uuid.UUID, email: str) -> User:
    """A colleague on the `member` role — holds ``google.drive.write``, never ``.manage``."""
    from pwdlib import PasswordHash

    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=PasswordHash.recommended().hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, "member")
        session.add(
            GoogleConnection(
                org_id=org_id,
                user_id=user.id,
                google_sub=f"sub-{email}",
                email=email,
                scopes=["openid", "email", SCOPE_DRIVE],
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


async def test_picking_an_existing_folder_sets_the_client_folder(client_for, monkeypatch) -> None:
    """The picker's happy path: an existing Drive folder becomes *the* client folder, a file is
    refused, and a second linked folder never hijacks the decision."""
    t = await make_tenant("gdrive-pick")
    await _seed(t)
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()

        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-1", "Klant BV"))])),
        )
        picked = await c.put(
            "/api/v1/google/drive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_file_id": "folder-1",
            },
            headers=headers,
        )
        assert picked.status_code == 200, picked.text
        assert picked.json()["is_root"] is True and picked.json()["name"] == "Klant BV"

        # A file is not a folder — refused on the field, never stored to be puzzled over later.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(
                _StubClient(
                    [
                        (
                            "GET",
                            _StubResponse(
                                200,
                                {
                                    "id": "file-9",
                                    "name": "Offerte.pdf",
                                    "mimeType": "application/pdf",
                                },
                            ),
                        )
                    ]
                )
            ),
        )
        refused = await c.put(
            "/api/v1/google/drive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_file_id": "file-9",
            },
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "google_drive_not_a_folder"

        # A *subfolder* linked as an attachment stays an ordinary link and must not become the
        # client folder — the ambiguity `is_root` exists to remove.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-2", "Facturen"))])),
        )
        assert (
            await c.post(
                "/api/v1/google/drive/links",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_file_id": "folder-2",
                },
                headers=headers,
            )
        ).status_code == 201
        links = (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert [link["drive_file_id"] for link in links if link["is_root"]] == ["folder-1"]
        # The record's own folder sorts first, so "the folder" never depends on row order.
        assert links[0]["drive_file_id"] == "folder-1"

        # And the decision is on the client's trail (CLAUDE.md §16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        entry = next(item for item in trail if item["action"] == "drive.folder_set")
        assert entry["payload"]["name"] == "Klant BV"


async def test_replacing_or_detaching_a_folder_needs_manage(client_for, monkeypatch) -> None:
    """The permission boundary: a member gives a client its *first* folder, and can neither
    re-point nor detach one that is already set."""
    t = await make_tenant("gdrive-pickperm")
    await _seed(t)
    owner_headers = await auth_cookie(t.user)
    member = await _add_member_with_connection(t.org.id, "collega@gdrive-pickperm.test")
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        first = (
            await c.post("/api/v1/companies", json={"name": "Eerste"}, headers=owner_headers)
        ).json()
        second = (
            await c.post("/api/v1/companies", json={"name": "Tweede"}, headers=owner_headers)
        ).json()

        # A member may fill an empty slot: no folder yet, so this is ordinary write work.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-a", "Eerste"))])),
        )
        assert (
            await c.put(
                "/api/v1/google/drive/folder",
                json={
                    "entity_type": "company",
                    "entity_id": first["id"],
                    "drive_file_id": "folder-a",
                },
                headers=member_headers,
            )
        ).status_code == 200

        # Re-pointing it is a different act. Refused before any Drive call is made — the empty
        # stub script is the assertion that nothing was fetched.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(_StubClient([]))
        )
        denied = await c.put(
            "/api/v1/google/drive/folder",
            json={
                "entity_type": "company",
                "entity_id": first["id"],
                "drive_file_id": "folder-b",
            },
            headers=member_headers,
        )
        assert denied.status_code == 403, denied.text

        # Nor may they detach it through the ordinary unlink route — the same act, reached from
        # the other side.
        root_id = next(
            link["id"]
            for link in (
                await c.get(
                    "/api/v1/google/drive/links",
                    params={"entity_type": "company", "entity_id": first["id"]},
                    headers=member_headers,
                )
            ).json()
            if link["is_root"]
        )
        assert (
            await c.delete(f"/api/v1/google/drive/links/{root_id}", headers=member_headers)
        ).status_code == 403

        # The owner may do both.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-b", "Eerste v2"))])),
        )
        replaced = await c.put(
            "/api/v1/google/drive/folder",
            json={
                "entity_type": "company",
                "entity_id": first["id"],
                "drive_file_id": "folder-b",
            },
            headers=owner_headers,
        )
        assert replaced.status_code == 200, replaced.text
        after = (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "company", "entity_id": first["id"]},
                headers=owner_headers,
            )
        ).json()
        # One folder per record: the old one does not linger as a loose attachment.
        assert [link["drive_file_id"] for link in after] == ["folder-b"]

        # An unrelated client is untouched by any of it.
        assert (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "company", "entity_id": second["id"]},
                headers=owner_headers,
            )
        ).json() == []


async def test_provision_refuses_a_second_folder(client_for, monkeypatch) -> None:
    """A record has one folder: provisioning a second is refused rather than landing a folder
    in Drive that nothing points at."""
    t = await make_tenant("gdrive-second")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-1", "Klant BV"))])),
        )
        assert (
            await c.put(
                "/api/v1/google/drive/folder",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_file_id": "folder-1",
                },
                headers=headers,
            )
        ).status_code == 200

        response = await c.post(
            "/api/v1/google/drive/provision",
            json={"entity_type": "company", "entity_id": company["id"]},
            headers=headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "google_drive_folder_exists"


# --------------------------------------------------------------------------- #
# A task's own folder (#328): on demand only, nested under its project's folder,
# else its client's — the same walk the panel already sends the browser down.
# --------------------------------------------------------------------------- #
async def _company_project_task(client, headers) -> tuple[dict, dict, dict]:
    company = (
        await client.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
    ).json()
    project = (
        await client.post(
            "/api/v1/projects",
            json={"name": "Site", "company_id": company["id"]},
            headers=headers,
        )
    ).json()
    task = (
        await client.post(
            "/api/v1/tasks",
            json={
                "due_date": FAR_FUTURE_DUE,
                "title": "Logo aanleveren",
                "project_id": project["id"],
            },
            headers=headers,
        )
    ).json()
    return company, project, task


async def test_task_folder_is_provisioned_under_the_project_folder(
    client_for, monkeypatch
) -> None:
    """The route accepts ``task`` at all (it used to 422), and the worker nests the new folder
    inside the project's — not in the org root, where it would be indistinguishable from
    everything else the agency has ever made."""
    t = await make_tenant("gdrive-taskfolder")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with client_for(t.host) as c:
        _, project, task = await _company_project_task(c, headers)
        # The project has its own folder; the task has none.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("proj-folder", "Site"))])),
        )
        assert (
            await c.put(
                "/api/v1/google/drive/folder",
                json={
                    "entity_type": "project",
                    "entity_id": project["id"],
                    "drive_file_id": "proj-folder",
                },
                headers=headers,
            )
        ).status_code == 200

        queued = await c.post(
            "/api/v1/google/drive/provision",
            json={"entity_type": "task", "entity_id": task["id"]},
            headers=headers,
        )
        assert queued.status_code == 202, queued.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        job = (
            await session.execute(
                select(DriveFolderJob).where(DriveFolderJob.entity_type == "task")
            )
        ).scalar_one()
        # The parent is the *record*, resolved at emit; its folder is resolved below.
        assert job.parent_entity_type == "project"
        assert str(job.parent_entity_id) == project["id"]
        assert job.name == "Logo aanleveren"

        stub = _StubClient(
            [
                ("GET", _StubResponse(200, {"files": []})),
                (
                    "POST",
                    _StubResponse(
                        200,
                        {
                            "id": "task-folder",
                            "name": "Logo aanleveren",
                            "webViewLink": "https://drive.google.com/drive/folders/task-folder",
                        },
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(stub),
        )
        await provision_folder(session, t.org, job)

        assert stub.call_kwargs[-1]["json"]["parents"] == ["proj-folder"]
        link = (
            await session.execute(
                select(DriveLink).where(DriveLink.entity_type == "task")
            )
        ).scalar_one()
        assert link.drive_file_id == "task-folder" and link.is_root is True
        assert str(link.entity_id) == task["id"]
        await session.commit()


async def test_task_folder_falls_back_to_the_client_folder(client_for, monkeypatch) -> None:
    """A project that never got a folder of its own is not a dead end: the client's folder is
    the next honest answer, and it is where the panel's browser already opens."""
    t = await make_tenant("gdrive-taskfallback")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    async with client_for(t.host) as c:
        company, _project, task = await _company_project_task(c, headers)
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("klant-folder", "Klant BV"))])),
        )
        assert (
            await c.put(
                "/api/v1/google/drive/folder",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_file_id": "klant-folder",
                },
                headers=headers,
            )
        ).status_code == 200
        assert (
            await c.post(
                "/api/v1/google/drive/provision",
                json={"entity_type": "task", "entity_id": task["id"]},
                headers=headers,
            )
        ).status_code == 202

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        job = (
            await session.execute(
                select(DriveFolderJob).where(DriveFolderJob.entity_type == "task")
            )
        ).scalar_one()
        stub = _StubClient(
            [
                ("GET", _StubResponse(200, {"files": []})),
                ("POST", _StubResponse(200, {"id": "task-folder", "name": "Logo aanleveren"})),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(stub),
        )
        await provision_folder(session, t.org, job)
        await session.commit()
        assert stub.call_kwargs[-1]["json"]["parents"] == ["klant-folder"]


async def test_task_provision_is_tenant_scoped(client_for, monkeypatch) -> None:
    """Every Drive surface is entity-addressed (§15's failure mode 4): another org's task id
    answers 404, and no job is written for it."""
    a = await make_tenant("gdrive-task-iso-a")
    b = await make_tenant("gdrive-task-iso-b")
    await _seed(a, automation=True)
    await _seed(b, automation=True)

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)

    a_headers = await auth_cookie(a.user)
    async with client_for(a.host) as ca:
        _, _, task = await _company_project_task(ca, a_headers)

    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        response = await cb.post(
            "/api/v1/google/drive/provision",
            json={"entity_type": "task", "entity_id": task["id"]},
            headers=b_headers,
        )
        assert response.status_code == 404, response.text

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        from sqlalchemy import select

        assert (await session.execute(select(DriveFolderJob))).scalars().all() == []


async def test_task_drive_links_stay_one_query_however_many_files(
    client_for, count_queries
) -> None:
    """docs/PERFORMANCE.md: the task panel's whole SSR load is this call, and an upload now
    adds a row to it on every upload. One statement at one file and one at twenty, or the
    panel gets slower every time somebody uses the feature this issue added."""
    t = await make_tenant("gdrive-task-budget")
    await _seed(t)
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        _, _, task = await _company_project_task(c, headers)
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            for i in range(20):
                session.add(
                    DriveLink(
                        org_id=t.org.id,
                        entity_type="task",
                        entity_id=uuid.UUID(task["id"]),
                        drive_file_id=f"file-{i}",
                        drive_url=f"https://drive/{i}",
                        name=f"Bestand {i}.pdf",
                    )
                )
            await session.commit()

        with count_queries() as counter:
            listed = await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "task", "entity_id": task["id"]},
                headers=headers,
            )
        assert listed.status_code == 200
        assert len(listed.json()) == 20
        assert len(counter.matching("from drive_links")) == 1


# --- trashing a file in Drive (#394) --------------------------------------------------- #
def _file_meta(
    file_id: str = "file-1",
    name: str = "Offerte.pdf",
    *,
    mime: str = "application/pdf",
    parents: list[str] | None = None,
    trashed: bool = False,
) -> _StubResponse:
    return _StubResponse(
        200,
        {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": parents or ["parent-1"],
            "trashed": trashed,
            "webViewLink": f"https://drive.google.com/file/d/{file_id}",
            "driveId": "sd-1",
        },
    )


async def test_trashing_bins_the_file_and_drops_every_link_org_wide(
    client_for, monkeypatch
) -> None:
    """#394: the other half of unlink. Drive gets ``trashed: true`` — never ``files.delete`` —
    and every ``drive_links`` row naming that file goes, for *both* records that linked it."""
    t = await make_tenant("gdrive-trash")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        task = (
            await c.post("/api/v1/tasks", json={"title": "Review"}, headers=headers)
        ).json()

        # One file, linked to two different records.
        for entity_type, entity_id in (("company", company["id"]), ("task", task["id"])):
            monkeypatch.setattr(
                "app.integrations.google.drive.service.acting_as",
                _stub_acting_as(_StubClient([("GET", _file_meta())])),
            )
            created = await c.post(
                "/api/v1/google/drive/links",
                json={
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "drive_file_id": "file-1",
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text

        stub = _StubClient([("GET", _file_meta()), ("PATCH", _StubResponse(200, {"id": "file-1"}))])
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
        )
        trashed = await c.delete("/api/v1/google/drive/files/file-1", headers=headers)
        assert trashed.status_code == 204, trashed.text
        # Trash, not purge: Drive keeps it recoverable for the owner for 30 days.
        assert [method for method, _ in stub.calls] == ["GET", "PATCH"]
        assert stub.call_kwargs[-1]["json"] == {"trashed": True}

        # Both links are gone — a link to a trashed file renders a name and 404s when clicked.
        for entity_type, entity_id in (("company", company["id"]), ("task", task["id"])):
            assert (
                await c.get(
                    "/api/v1/google/drive/links",
                    params={"entity_type": entity_type, "entity_id": entity_id},
                    headers=headers,
                )
            ).json() == []

        # And the record says what happened to it (CLAUDE.md §16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert any(entry["action"] == "drive.file_trashed" for entry in trail)


async def test_trashing_refuses_a_folder_that_is_not_empty(client_for, monkeypatch) -> None:
    """A delete that silently took a client's whole project folder with it is the worst
    control available on that panel — so the emptiness check comes before the write."""
    t = await make_tenant("gdrive-trashdir")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    async with client_for(t.host) as c:
        occupied = _StubClient(
            [
                ("GET", _file_meta("folder-x", "Projecten", mime=FOLDER_MIME)),
                ("GET", _StubResponse(200, {"files": [{"id": "inside-1"}]})),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(occupied)
        )
        refused = await c.delete("/api/v1/google/drive/files/folder-x", headers=headers)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["message"] == "errors.google_drive_folder_not_empty"
        # Nothing was written: the script holds no PATCH and the stub is exhausted.
        assert [method for method, _ in occupied.calls] == ["GET", "GET"]

        # An empty one goes.
        empty = _StubClient(
            [
                ("GET", _file_meta("folder-y", "Leeg", mime=FOLDER_MIME)),
                ("GET", _StubResponse(200, {"files": []})),
                ("PATCH", _StubResponse(200, {"id": "folder-y"})),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(empty)
        )
        assert (
            await c.delete("/api/v1/google/drive/files/folder-y", headers=headers)
        ).status_code == 204


async def test_trashing_reports_drives_own_refusal_in_its_own_words(
    client_for, monkeypatch
) -> None:
    """*May not open* and *may not delete* have different cures and different people who grant
    them, so the delete path answers with its own key rather than the folder-access one."""
    t = await make_tenant("gdrive-trash403")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    async with client_for(t.host) as c:
        stub = _StubClient(
            [
                ("GET", _file_meta()),
                (
                    "PATCH",
                    _google_error(
                        403, None, "The user does not have sufficient permissions for this file."
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(stub)
        )
        refused = await c.delete("/api/v1/google/drive/files/file-1", headers=headers)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["message"] == "errors.google_drive_delete_forbidden"

        # A file this account cannot see at all is a 404, not a permission sentence.
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _StubResponse(404, {}))])),
        )
        assert (
            await c.delete("/api/v1/google/drive/files/file-9", headers=headers)
        ).status_code == 404


async def test_trashing_a_records_own_folder_needs_manage(client_for, monkeypatch) -> None:
    """Detaching a record's folder is ``google.drive.manage`` (docs/GOOGLE.md §5) and binning
    it is strictly the larger act, so it cannot ask for less — and it is refused *before* the
    file is in the bin, which an empty stub script is the assertion for."""
    t = await make_tenant("gdrive-trashroot")
    await _seed(t)
    owner_headers = await auth_cookie(t.user)
    member = await _add_member_with_connection(t.org.id, "collega@gdrive-trashroot.test")
    member_headers = await auth_cookie(member)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=owner_headers)
        ).json()
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(_StubClient([("GET", _folder_meta("folder-a", "Klant BV"))])),
        )
        assert (
            await c.put(
                "/api/v1/google/drive/folder",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_file_id": "folder-a",
                },
                headers=owner_headers,
            )
        ).status_code == 200

        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(_StubClient([]))
        )
        denied = await c.delete("/api/v1/google/drive/files/folder-a", headers=member_headers)
        assert denied.status_code == 403, denied.text

        # The owner may, and the record's folder is cleared along with the link.
        owner_stub = _StubClient(
            [
                ("GET", _file_meta("folder-a", "Klant BV", mime=FOLDER_MIME)),
                ("GET", _StubResponse(200, {"files": []})),
                ("PATCH", _StubResponse(200, {"id": "folder-a"})),
            ]
        )
        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as", _stub_acting_as(owner_stub)
        )
        assert (
            await c.delete("/api/v1/google/drive/files/folder-a", headers=owner_headers)
        ).status_code == 204
        assert (
            await c.get(
                "/api/v1/google/drive/links",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=owner_headers,
            )
        ).json() == []


async def test_trashing_is_tenant_scoped(client_for, monkeypatch) -> None:
    """Golden Rule 1: another org's link to the same Drive file is not this org's to remove."""
    a = await make_tenant("gdrive-trash-a")
    b = await make_tenant("gdrive-trash-b")
    await _seed(a)
    await _seed(b)
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    monkeypatch.setattr("app.integrations.google.drive.service.get_redis", lambda: _FakeRedis())

    async with client_for(a.host) as ca, client_for(b.host) as cb:
        company_a = (
            await ca.post("/api/v1/companies", json={"name": "A"}, headers=a_headers)
        ).json()
        company_b = (
            await cb.post("/api/v1/companies", json={"name": "B"}, headers=b_headers)
        ).json()
        for c, host_headers, company in (
            (ca, a_headers, company_a),
            (cb, b_headers, company_b),
        ):
            monkeypatch.setattr(
                "app.integrations.google.drive.service.acting_as",
                _stub_acting_as(_StubClient([("GET", _file_meta("shared-1", "Gedeeld.pdf"))])),
            )
            assert (
                await c.post(
                    "/api/v1/google/drive/links",
                    json={
                        "entity_type": "company",
                        "entity_id": company["id"],
                        "drive_file_id": "shared-1",
                    },
                    headers=host_headers,
                )
            ).status_code == 201

        monkeypatch.setattr(
            "app.integrations.google.drive.service.acting_as",
            _stub_acting_as(
                _StubClient(
                    [("GET", _file_meta("shared-1")), ("PATCH", _StubResponse(200, {"id": "x"}))]
                )
            ),
        )
        assert (
            await ca.delete("/api/v1/google/drive/files/shared-1", headers=a_headers)
        ).status_code == 204

        # Org B's row survives: "org-wide" means this org, never the instance.
        assert [
            link["drive_file_id"]
            for link in (
                await cb.get(
                    "/api/v1/google/drive/links",
                    params={"entity_type": "company", "entity_id": company_b["id"]},
                    headers=b_headers,
                )
            ).json()
        ] == ["shared-1"]
