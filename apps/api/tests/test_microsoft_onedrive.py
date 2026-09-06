"""microsoft.onedrive: links + rollup, browse cache, folder picker, recycle bin, provisioning."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.core.crypto import encrypt
from app.core.events import SystemContext, emit
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.models import MicrosoftConnection, MicrosoftSettings
from app.integrations.microsoft.oauth import SCOPE_FILES, SCOPE_IDENTITY
from app.integrations.microsoft.onedrive import service as onedrive_service
from app.integrations.microsoft.onedrive.models import OneDriveFolderJob, OneDriveLink
from app.integrations.microsoft.onedrive.service import provision_folder
from tests.conftest import FAR_FUTURE_DUE, add_membership, auth_cookie, make_tenant

ACTING_AS = "app.integrations.microsoft.onedrive.service.acting_as"
REDIS = "app.integrations.microsoft.onedrive.service.get_redis"


class _StubResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None, headers=None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = ""

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://graph.example/x")
            response = httpx.Response(self.status_code, json=self._body, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


class _StubClient:
    """Scripted Graph: each GET/POST/PUT/PATCH/DELETE pops the next queued response."""

    def __init__(self, script: list[tuple[str, _StubResponse]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []
        self.call_kwargs: list[dict] = []

    async def _pop(self, method: str, url: str, **kwargs) -> _StubResponse:
        self.calls.append((method, url))
        self.call_kwargs.append(kwargs)
        assert self.script, f"unexpected Graph call: {method} {url}"
        expected, response = self.script.pop(0)
        assert expected == method, f"expected {expected}, got {method} {url}"
        return response

    async def get(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("PATCH", url, **kwargs)

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

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _folder(item_id: str, name: str, *, drive: str = "drv-1") -> dict:
    return {
        "id": item_id,
        "name": name,
        "folder": {"childCount": 0},
        "webUrl": f"https://tenant.sharepoint.com/{name}",
        "parentReference": {"driveId": drive},
    }


def _file(item_id: str, name: str, mime: str = "application/pdf", *, drive: str = "drv-1") -> dict:
    return {
        "id": item_id,
        "name": name,
        "file": {"mimeType": mime},
        "size": 1024,
        "webUrl": f"https://tenant.sharepoint.com/{name}",
        "lastModifiedDateTime": "2026-07-01T10:00:00Z",
        "parentReference": {"driveId": drive},
    }


async def _seed(
    tenant,
    *,
    auto_provision: bool = False,
    automation: bool = False,
    drive: str | None = "drv-1",
    parent: str | None = "parent-1",
    template: str | None = None,
    files_scope: bool = True,
    user_id: uuid.UUID | None = None,
) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            MicrosoftSettings(
                org_id=tenant.org.id,
                onedrive_enabled=True,
                onedrive_drive_id=drive,
                onedrive_parent_folder_id=parent,
                onedrive_template_folder_id=template,
                onedrive_auto_provision=auto_provision,
                automation_connection_user_id=tenant.user.id if automation else None,
            )
        )
        session.add(
            MicrosoftConnection(
                org_id=tenant.org.id,
                user_id=user_id or tenant.user.id,
                microsoft_oid="oid-1",
                email="me@agency.nl",
                scopes=[SCOPE_IDENTITY, *([SCOPE_FILES] if files_scope else [])],
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()


async def _company(c, headers, name: str = "Klant BV") -> dict:
    created = await c.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()


async def _trail(org_id: uuid.UUID, entity_id: uuid.UUID) -> list[str]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            select(ActivityLog.action).where(
                ActivityLog.org_id == org_id, ActivityLog.entity_id == entity_id
            )
        )
        return [row[0] for row in rows]


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
async def test_links_crud_rollup_and_unlink_never_deletes(client_for, monkeypatch) -> None:
    t = await make_tenant("od-links")
    await _seed(t)
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = await _company(c, headers)
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

        stub = _StubClient([("GET", _StubResponse(200, _file("file-1", "Offerte.pdf")))])
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        created = await c.post(
            "/api/v1/microsoft/onedrive/links",
            json={
                "entity_type": "task",
                "entity_id": task["id"],
                "drive_id": "drv-1",
                "item_id": "file-1",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        link = created.json()
        assert link["name"] == "Offerte.pdf" and link["is_folder"] is False
        assert link["drive_id"] == "drv-1" and link["item_id"] == "file-1"
        # The item is addressed by both ids, and the metadata read went to that address.
        assert stub.calls == [("GET", "/drives/drv-1/items/file-1")]

        # Roll-up: the task's file surfaces on its project.
        rolled = (
            await c.get(
                "/api/v1/microsoft/onedrive/links",
                params={"entity_type": "project", "entity_id": project["id"], "rollup": True},
                headers=headers,
            )
        ).json()
        assert [item["item_id"] for item in rolled] == ["file-1"]
        flat = (
            await c.get(
                "/api/v1/microsoft/onedrive/links",
                params={"entity_type": "project", "entity_id": project["id"]},
                headers=headers,
            )
        ).json()
        assert flat == []

        # Unlink: 204, the reference is gone, and the empty stub script proves no Graph call.
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([])))
        assert (
            await c.delete(f"/api/v1/microsoft/onedrive/links/{link['id']}", headers=headers)
        ).status_code == 204
        assert (
            await c.get(
                "/api/v1/microsoft/onedrive/links",
                params={"entity_type": "task", "entity_id": task["id"]},
                headers=headers,
            )
        ).json() == []


async def test_links_are_tenant_scoped(client_for) -> None:
    a = await make_tenant("od-iso-a")
    b = await make_tenant("od-iso-b")
    await _seed(a)
    await _seed(b)
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        session.add(
            OneDriveLink(
                org_id=a.org.id,
                entity_type="company",
                entity_id=uuid.UUID(company["id"]),
                drive_id="drv-1",
                item_id="folder-a",
                web_url="https://x",
                name="Map A",
                is_folder=True,
                is_root=True,
            )
        )
        await session.commit()
    async with client_for(b.host) as cb:
        # RLS: the other tenant's links never come back, whatever id is asked about.
        response = await cb.get(
            "/api/v1/microsoft/onedrive/links",
            params={"entity_type": "company", "entity_id": company["id"]},
            headers=b_headers,
        )
        assert response.status_code == 200 and response.json() == []


# --------------------------------------------------------------------------- #
# Browse
# --------------------------------------------------------------------------- #
async def test_browse_caches_busts_and_puts_folders_first(client_for, monkeypatch) -> None:
    t = await make_tenant("od-browse")
    await _seed(t)
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(REDIS, lambda: fake_redis)

    # Graph orders folders and files together by name; the browser wants folders first.
    listing = _StubResponse(
        200,
        {
            "value": [
                _file("f-1", "Aanvraag.pdf"),
                _folder("sub-1", "Contracten"),
                _file("f-2", "Logo.png", "image/png"),
            ]
        },
    )
    folder_meta = _StubResponse(200, _folder("parent-1", "Klanten"))
    stub = _StubClient([("GET", listing), ("GET", folder_meta)])
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))

    async with client_for(t.host) as c:
        first = (await c.get("/api/v1/microsoft/onedrive/browse", headers=headers)).json()
        assert first["folder"]["name"] == "Klanten" and first["folder"]["drive_id"] == "drv-1"
        assert [item["name"] for item in first["items"]] == [
            "Contracten",
            "Aanvraag.pdf",
            "Logo.png",
        ]
        assert first["items"][0]["is_folder"] is True
        assert first["items"][1]["mime_type"] == "application/pdf"
        assert first["truncated"] is False
        # With nothing named, the browse starts at the configured parent in the configured drive.
        assert stub.calls[0] == ("GET", "/drives/drv-1/items/parent-1/children")
        assert stub.call_kwargs[0]["params"]["$orderby"] == "name"

        # Second read comes from the cache — the exhausted stub proves no second Graph call.
        second = (await c.get("/api/v1/microsoft/onedrive/browse", headers=headers)).json()
        assert second == first

        stub2 = _StubClient([("GET", listing), ("GET", folder_meta)])
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub2))
        refreshed = await c.get(
            "/api/v1/microsoft/onedrive/browse", params={"refresh": True}, headers=headers
        )
        assert refreshed.status_code == 200 and stub2.script == []


async def test_browse_search_is_graphs_and_says_when_it_is_a_prefix(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("od-search")
    await _seed(t)
    org_id, user_id = t.org.id, t.user.id
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(REDIS, lambda: fake_redis)

    hits = _StubResponse(
        200,
        {
            "value": [_file("f-9", "O'Neill offerte.pdf")],
            "@odata.nextLink": "https://graph/next",
        },
    )
    folder_meta = _StubResponse(200, _folder("parent-1", "Klanten"))
    stub = _StubClient([("GET", hits), ("GET", folder_meta)])
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))

    async with client_for(t.host) as c:
        searched = await c.get(
            "/api/v1/microsoft/onedrive/browse", params={"q": "o'neill"}, headers=headers
        )
        assert searched.status_code == 200, searched.text
        body = searched.json()
        assert [item["name"] for item in body["items"]] == ["O'Neill offerte.pdf"]
        assert body["query"] == "o'neill"
        # Graph had another page we did not follow: the list says it is a prefix.
        assert body["truncated"] is True
        # The search ran at Graph, over this folder, with the quote doubled the OData way.
        assert stub.calls[0] == ("GET", "/drives/drv-1/items/parent-1/search(q='o''neill')")

    # The search entry and the folder's own entry are two different keys.
    assert sorted(fake_redis.store) == [
        f"schakl:onedrive:browse:{org_id}:{user_id}:drv-1:parent-1:o'neill",
    ]


async def test_browse_reports_graphs_own_reason_and_refuses_without_scope(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("od-403")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(REDIS, lambda: _FakeRedis())
    refused = _StubResponse(
        403, {"error": {"code": "accessDenied", "message": "Access denied to drv-1"}}
    )
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([("GET", refused)])))
    async with client_for(t.host) as c:
        response = await c.get("/api/v1/microsoft/onedrive/browse", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "errors.microsoft_onedrive_scope_missing"

        gone = _StubResponse(404, {"error": {"code": "itemNotFound", "message": "gone"}})
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([("GET", gone)])))
        assert (
            await c.get(
                "/api/v1/microsoft/onedrive/browse",
                params={"drive_id": "drv-1", "folder_id": "nope", "refresh": True},
                headers=headers,
            )
        ).status_code == 404

    # A connection minted before OneDrive was on is refused before any round trip.
    t2 = await make_tenant("od-noscope")
    await _seed(t2, files_scope=False)
    headers2 = await auth_cookie(t2.user)
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([])))
    async with client_for(t2.host) as c:
        response = await c.get("/api/v1/microsoft/onedrive/browse", headers=headers2)
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "errors.microsoft_onedrive_scope_missing"


# --------------------------------------------------------------------------- #
# The record's folder
# --------------------------------------------------------------------------- #
async def test_picking_a_folder_promotes_and_refuses_a_file(client_for, monkeypatch) -> None:
    t = await make_tenant("od-pick")
    await _seed(t)
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = await _company(c, headers)
        # A plain attachment first (a folder linked to a folderless record claims the root —
        # so link a file, then pick a folder).
        monkeypatch.setattr(
            ACTING_AS,
            _stub_acting_as(_StubClient([("GET", _StubResponse(200, _file("f-1", "Nota.pdf")))])),
        )
        assert (
            await c.post(
                "/api/v1/microsoft/onedrive/links",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_id": "drv-1",
                    "item_id": "f-1",
                },
                headers=headers,
            )
        ).status_code == 201

        # Refuse a file as a folder, with the field named.
        monkeypatch.setattr(
            ACTING_AS,
            _stub_acting_as(_StubClient([("GET", _StubResponse(200, _file("f-1", "Nota.pdf")))])),
        )
        refused = await c.put(
            "/api/v1/microsoft/onedrive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_id": "drv-1",
                "item_id": "f-1",
            },
            headers=headers,
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["fields"]["item_id"] == (
            "errors.microsoft_onedrive_not_a_folder"
        )

        # Pick a real folder: it becomes the record's root, and the trail says so.
        monkeypatch.setattr(
            ACTING_AS,
            _stub_acting_as(
                _StubClient([("GET", _StubResponse(200, _folder("fold-1", "Klant BV")))])
            ),
        )
        picked = await c.put(
            "/api/v1/microsoft/onedrive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_id": "drv-1",
                "item_id": "fold-1",
            },
            headers=headers,
        )
        assert picked.status_code == 200, picked.text
        assert picked.json()["is_root"] is True and picked.json()["is_folder"] is True

        links = (
            await c.get(
                "/api/v1/microsoft/onedrive/links",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        # The root leads the list, and picking again the same folder changes nothing.
        assert [link["item_id"] for link in links] == ["fold-1", "f-1"]
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([])))
        assert (
            await c.put(
                "/api/v1/microsoft/onedrive/folder",
                json={
                    "entity_type": "company",
                    "entity_id": company["id"],
                    "drive_id": "drv-1",
                    "item_id": "fold-1",
                },
                headers=headers,
            )
        ).status_code == 200
    assert "onedrive.folder_set" in await _trail(t.org.id, uuid.UUID(company["id"]))


async def test_replacing_or_detaching_a_folder_needs_manage(client_for, monkeypatch) -> None:
    t = await make_tenant("od-manage")
    await _seed(t)
    owner_h = await auth_cookie(t.user)

    # A member: holds write (default) but not manage.
    async with async_session_maker() as session:
        member = User(
            id=uuid.uuid4(),
            email="lid@agency.nl",
            hashed_password="x",
            is_active=True,
            is_verified=True,
        )
        session.add(member)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.id, "member")
        session.add(
            MicrosoftConnection(
                org_id=t.org.id,
                user_id=member.id,
                microsoft_oid="oid-2",
                email="lid@agency.nl",
                scopes=[SCOPE_IDENTITY, SCOPE_FILES],
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()
    member_h = await auth_cookie(member)

    async with client_for(t.host) as c:
        company = await _company(c, owner_h)
        # The member gives the record its first folder: ordinary write work.
        monkeypatch.setattr(
            ACTING_AS,
            _stub_acting_as(
                _StubClient([("GET", _StubResponse(200, _folder("fold-1", "Klant BV")))])
            ),
        )
        first = await c.put(
            "/api/v1/microsoft/onedrive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_id": "drv-1",
                "item_id": "fold-1",
            },
            headers=member_h,
        )
        assert first.status_code == 200, first.text
        link_id = first.json()["id"]

        # Replacing it is refused for the member before any Graph call is made…
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([])))
        replaced = await c.put(
            "/api/v1/microsoft/onedrive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_id": "drv-1",
                "item_id": "fold-2",
            },
            headers=member_h,
        )
        assert replaced.status_code == 403, replaced.text
        # …and so is detaching it.
        assert (
            await c.delete(f"/api/v1/microsoft/onedrive/links/{link_id}", headers=member_h)
        ).status_code == 403

        # The owner (manage) replaces it, and the trail records the change.
        monkeypatch.setattr(
            ACTING_AS,
            _stub_acting_as(
                _StubClient([("GET", _StubResponse(200, _folder("fold-2", "Klant BV 2")))])
            ),
        )
        replaced = await c.put(
            "/api/v1/microsoft/onedrive/folder",
            json={
                "entity_type": "company",
                "entity_id": company["id"],
                "drive_id": "drv-1",
                "item_id": "fold-2",
            },
            headers=owner_h,
        )
        assert replaced.status_code == 200, replaced.text
        links = (
            await c.get(
                "/api/v1/microsoft/onedrive/links",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=owner_h,
            )
        ).json()
        assert [link["item_id"] for link in links] == ["fold-2"]
    trail = await _trail(t.org.id, uuid.UUID(company["id"]))
    assert "onedrive.folder_set" in trail and "onedrive.folder_changed" in trail


# --------------------------------------------------------------------------- #
# Recycle bin
# --------------------------------------------------------------------------- #
async def test_trashing_recycles_and_drops_every_link_org_wide(client_for, monkeypatch) -> None:
    t = await make_tenant("od-trash")
    await _seed(t)
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(REDIS, lambda: fake_redis)

    async with client_for(t.host) as c:
        first = await _company(c, headers, "Een")
        second = await _company(c, headers, "Twee")
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            for company in (first, second):
                session.add(
                    OneDriveLink(
                        org_id=t.org.id,
                        entity_type="company",
                        entity_id=uuid.UUID(company["id"]),
                        drive_id="drv-1",
                        item_id="f-1",
                        web_url="https://x",
                        name="Gedeeld.pdf",
                        is_folder=False,
                    )
                )
            await session.commit()

        meta = _file("f-1", "Gedeeld.pdf")
        meta["parentReference"] = {"driveId": "drv-1", "id": "parent-1"}
        stub = _StubClient([("GET", _StubResponse(200, meta)), ("DELETE", _StubResponse(204))])
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        fake_redis.store[f"schakl:onedrive:browse:{t.org.id}:{t.user.id}:drv-1:parent-1:"] = "{}"
        gone = await c.delete("/api/v1/microsoft/onedrive/files/drv-1/f-1", headers=headers)
        assert gone.status_code == 204, gone.text
        assert stub.calls == [
            ("GET", "/drives/drv-1/items/f-1"),
            ("DELETE", "/drives/drv-1/items/f-1"),
        ]
        for company in (first, second):
            assert (
                await c.get(
                    "/api/v1/microsoft/onedrive/links",
                    params={"entity_type": "company", "entity_id": company["id"]},
                    headers=headers,
                )
            ).json() == []
            assert "onedrive.file_trashed" in await _trail(t.org.id, uuid.UUID(company["id"]))
        # The viewer's listing of the folder it sat in was busted.
        assert fake_redis.store == {}


async def test_trashing_refuses_a_non_empty_folder_and_names_graphs_refusal(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("od-trash-refuse")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(REDIS, lambda: _FakeRedis())

    async with client_for(t.host) as c:
        stub = _StubClient(
            [
                ("GET", _StubResponse(200, _folder("fold-1", "Vol"))),
                ("GET", _StubResponse(200, {"value": [{"id": "child-1"}]})),
            ]
        )
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        refused = await c.delete("/api/v1/microsoft/onedrive/files/drv-1/fold-1", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == (
            "errors.microsoft_onedrive_folder_not_empty"
        )
        # Nothing was deleted: the script held no DELETE and none was asked for.
        assert [method for method, _ in stub.calls] == ["GET", "GET"]

        # Graph's own refusal to delete gets the delete-flavoured key, not the read one.
        stub = _StubClient(
            [
                ("GET", _StubResponse(200, _file("f-2", "Beschermd.pdf"))),
                (
                    "DELETE",
                    _StubResponse(
                        403, {"error": {"code": "notAllowed", "message": "Delete denied"}}
                    ),
                ),
            ]
        )
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        refused = await c.delete("/api/v1/microsoft/onedrive/files/drv-1/f-2", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == (
            "errors.microsoft_onedrive_delete_forbidden"
        )


async def test_trashing_a_records_own_folder_needs_manage(client_for, monkeypatch) -> None:
    t = await make_tenant("od-trash-root")
    await _seed(t)
    owner_h = await auth_cookie(t.user)
    async with async_session_maker() as session:
        member = User(
            id=uuid.uuid4(),
            email="lid@agency.nl",
            hashed_password="x",
            is_active=True,
            is_verified=True,
        )
        session.add(member)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.id, "member")
        session.add(
            MicrosoftConnection(
                org_id=t.org.id,
                user_id=member.id,
                microsoft_oid="oid-2",
                email="lid@agency.nl",
                scopes=[SCOPE_IDENTITY, SCOPE_FILES],
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()
    member_h = await auth_cookie(member)
    monkeypatch.setattr(REDIS, lambda: _FakeRedis())

    async with client_for(t.host) as c:
        company = await _company(c, owner_h)
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            session.add(
                OneDriveLink(
                    org_id=t.org.id,
                    entity_type="company",
                    entity_id=uuid.UUID(company["id"]),
                    drive_id="drv-1",
                    item_id="fold-1",
                    web_url="https://x",
                    name="Klant BV",
                    is_folder=True,
                    is_root=True,
                )
            )
            await session.commit()
        # Checked before any round-trip: the empty script proves it.
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(_StubClient([])))
        assert (
            await c.delete("/api/v1/microsoft/onedrive/files/drv-1/fold-1", headers=member_h)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# Upload session and subfolders
# --------------------------------------------------------------------------- #
async def test_upload_session_and_create_folder(client_for, monkeypatch) -> None:
    t = await make_tenant("od-upload")
    await _seed(t)
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(REDIS, lambda: fake_redis)

    async with client_for(t.host) as c:
        stub = _StubClient(
            [("POST", _StubResponse(200, {"uploadUrl": "https://upload.graph/session-1"}))]
        )
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        minted = await c.post(
            "/api/v1/microsoft/onedrive/upload-session",
            json={
                "drive_id": "drv-1",
                "folder_id": "parent-1",
                "name": "Scan.pdf",
                "mime_type": "application/pdf",
            },
            headers=headers,
        )
        assert minted.status_code == 200, minted.text
        assert minted.json()["session_uri"] == "https://upload.graph/session-1"
        assert stub.calls == [
            ("POST", "/drives/drv-1/items/parent-1:/Scan.pdf:/createUploadSession")
        ]
        assert stub.call_kwargs[0]["json"]["item"]["@microsoft.graph.conflictBehavior"] == "rename"

        # New folder: name-match first (a 404 means none), then create; the listing cache
        # of the parent is busted.
        fake_redis.store[f"schakl:onedrive:browse:{t.org.id}:{t.user.id}:drv-1:parent-1:"] = "{}"
        stub = _StubClient(
            [
                ("GET", _StubResponse(404, {"error": {"code": "itemNotFound", "message": "x"}})),
                ("POST", _StubResponse(201, _folder("new-1", "Contracten"))),
            ]
        )
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        created = await c.post(
            "/api/v1/microsoft/onedrive/folders",
            json={"drive_id": "drv-1", "parent_id": "parent-1", "name": "Contracten"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json() == {
            "id": "new-1",
            "drive_id": "drv-1",
            "name": "Contracten",
            "web_view_link": "https://tenant.sharepoint.com/Contracten",
        }
        assert stub.calls == [
            ("GET", "/drives/drv-1/items/parent-1:/Contracten"),
            ("POST", "/drives/drv-1/items/parent-1/children"),
        ]
        assert stub.call_kwargs[1]["json"]["@microsoft.graph.conflictBehavior"] == "fail"
        assert fake_redis.store == {}

        # Re-typing an existing name links to it rather than duplicating.
        stub = _StubClient([("GET", _StubResponse(200, _folder("new-1", "Contracten")))])
        monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
        again = await c.post(
            "/api/v1/microsoft/onedrive/folders",
            json={"drive_id": "drv-1", "parent_id": "parent-1", "name": "Contracten"},
            headers=headers,
        )
        assert again.status_code == 201 and again.json()["id"] == "new-1"


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #
async def test_provision_request_refusals(client_for, monkeypatch) -> None:
    monkeypatch.setattr(REDIS, lambda: _FakeRedis())

    async def _queue(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("nothing may be queued on a refusal")

    monkeypatch.setattr("app.integrations.microsoft.onedrive.service.queue_folder_job", _queue)

    # No automation account.
    t = await make_tenant("od-prov-auto")
    await _seed(t, automation=False)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        refused = await c.post(
            "/api/v1/microsoft/onedrive/provision",
            json={"entity_type": "company", "entity_id": company["id"]},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.microsoft_no_automation_connection"

    # No drive at all — a parent folder id alone is not a root, because a folder id names
    # nothing without the drive it lives in.
    t2 = await make_tenant("od-prov-root")
    await _seed(t2, automation=True, drive=None, parent="parent-1")
    headers2 = await auth_cookie(t2.user)
    async with client_for(t2.host) as c:
        company = await _company(c, headers2)
        refused = await c.post(
            "/api/v1/microsoft/onedrive/provision",
            json={"entity_type": "company", "entity_id": company["id"]},
            headers=headers2,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.microsoft_onedrive_no_folder"

    # Already has a folder.
    t3 = await make_tenant("od-prov-exists")
    await _seed(t3, automation=True)
    headers3 = await auth_cookie(t3.user)
    async with client_for(t3.host) as c:
        company = await _company(c, headers3)
        async with async_session_maker() as session:
            await set_current_org(session, t3.org.id)
            session.add(
                OneDriveLink(
                    org_id=t3.org.id,
                    entity_type="company",
                    entity_id=uuid.UUID(company["id"]),
                    drive_id="drv-1",
                    item_id="fold-1",
                    web_url="https://x",
                    name="Klant BV",
                    is_folder=True,
                    is_root=True,
                )
            )
            await session.commit()
        refused = await c.post(
            "/api/v1/microsoft/onedrive/provision",
            json={"entity_type": "company", "entity_id": company["id"]},
            headers=headers3,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.microsoft_onedrive_folder_exists"


async def _queued_jobs(org_id: uuid.UUID) -> list[OneDriveFolderJob]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return list((await session.execute(select(OneDriveFolderJob))).scalars().all())


async def test_company_created_queues_only_with_auto_provision(monkeypatch) -> None:
    async def _no_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _no_enqueue)

    off = await make_tenant("od-auto-off")
    await _seed(off, auto_provision=False, automation=True)
    async with async_session_maker() as session:
        await set_current_org(session, off.org.id)
        await emit(
            "company.created",
            SystemContext(org=off.org, session=session),
            {"company_id": uuid.uuid4(), "status": "active", "title": "Nieuw", "_recipients": []},
        )
        await session.commit()
    assert await _queued_jobs(off.org.id) == []

    on = await make_tenant("od-auto-on")
    await _seed(on, auto_provision=True, automation=True)
    company_id = uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, on.org.id)
        await emit(
            "company.created",
            SystemContext(org=on.org, session=session),
            {"company_id": company_id, "status": "active", "title": "Nieuw", "_recipients": []},
        )
        await session.commit()
    jobs = await _queued_jobs(on.org.id)
    assert len(jobs) == 1 and jobs[0].entity_id == company_id and jobs[0].name == "Nieuw"


async def test_worker_creates_the_folder_and_links_it_as_root(monkeypatch) -> None:
    t = await make_tenant("od-worker")
    await _seed(t, automation=True)
    company_id = uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = OneDriveFolderJob(
            org_id=t.org.id, entity_type="company", entity_id=company_id, name="Klant BV"
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    stub = _StubClient(
        [
            ("GET", _StubResponse(404, {"error": {"code": "itemNotFound", "message": "x"}})),
            ("POST", _StubResponse(201, _folder("fold-9", "Klant BV"))),
        ]
    )
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = await session.get(OneDriveFolderJob, job_id)
        await provision_folder(session, t.org, job)
        # Read inside the transaction the RLS GUC is bound to (a commit ends it).
        assert job.status == "done" and job.last_error is None
        link = (await session.execute(select(OneDriveLink))).scalar_one()
        assert link.item_id == "fold-9" and link.drive_id == "drv-1"
        assert link.is_root is True and link.is_folder is True
        assert link.entity_id == company_id
        await session.commit()
    # Name-matched under the configured parent, then created there.
    assert stub.calls == [
        ("GET", "/drives/drv-1/items/parent-1:/Klant BV"),
        ("POST", "/drives/drv-1/items/parent-1/children"),
    ]


async def test_worker_copies_the_template_and_waits_for_the_monitor(monkeypatch) -> None:
    """A template copy is asynchronous at Graph: a 202 plus a monitor URL fetched without
    authentication until it says ``completed``."""
    t = await make_tenant("od-template")
    await _seed(t, automation=True, template="tmpl-1")
    company_id = uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = OneDriveFolderJob(
            org_id=t.org.id, entity_type="company", entity_id=company_id, name="Klant BV"
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    monitor_calls: list[str] = []

    class _Monitor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):  # noqa: ANN002
            return None

        async def get(self, url: str):
            monitor_calls.append(url)
            body = (
                {"status": "inProgress", "percentageComplete": 50}
                if len(monitor_calls) == 1
                else {"status": "completed", "resourceId": "copy-1"}
            )
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(onedrive_service.httpx, "AsyncClient", lambda **kw: _Monitor())  # noqa: ARG005
    monkeypatch.setattr(onedrive_service, "COPY_POLL_SECONDS", 0)
    stub = _StubClient(
        [
            ("GET", _StubResponse(404, {"error": {"code": "itemNotFound", "message": "x"}})),
            ("POST", _StubResponse(202, {}, headers={"Location": "https://graph/monitor/1"})),
            ("GET", _StubResponse(200, _folder("copy-1", "Klant BV"))),
        ]
    )
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = await session.get(OneDriveFolderJob, job_id)
        await provision_folder(session, t.org, job)
        assert job.status == "done", job.last_error
        link = (await session.execute(select(OneDriveLink))).scalar_one()
        assert link.item_id == "copy-1" and link.is_root
        await session.commit()
    assert stub.calls[1] == ("POST", "/drives/drv-1/items/tmpl-1/copy")
    assert stub.call_kwargs[1]["json"] == {
        "parentReference": {"driveId": "drv-1", "id": "parent-1"},
        "name": "Klant BV",
    }
    assert monitor_calls == ["https://graph/monitor/1", "https://graph/monitor/1"]


async def test_project_folder_nests_under_the_clients_root(monkeypatch) -> None:
    t = await make_tenant("od-nest")
    await _seed(t, automation=True)
    company_id, project_id = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            OneDriveLink(
                org_id=t.org.id,
                entity_type="company",
                entity_id=company_id,
                drive_id="drv-1",
                item_id="client-fold",
                web_url="https://x",
                name="Klant BV",
                is_folder=True,
                is_root=True,
            )
        )
        job = OneDriveFolderJob(
            org_id=t.org.id,
            entity_type="project",
            entity_id=project_id,
            name="Website",
            parent_entity_id=company_id,
            parent_entity_type="company",
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    stub = _StubClient(
        [
            ("GET", _StubResponse(200, _folder("proj-fold", "Website"))),
        ]
    )
    monkeypatch.setattr(ACTING_AS, _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = await session.get(OneDriveFolderJob, job_id)
        await provision_folder(session, t.org, job)
        await session.commit()
        assert job.status == "done"
    # Resolved at execution time, under the client's folder — and an existing folder of that
    # name is linked rather than duplicated.
    assert stub.calls == [("GET", "/drives/drv-1/items/client-fold:/Website")]


async def test_worker_skips_an_automation_account_without_the_files_scope() -> None:
    t = await make_tenant("od-skip")
    await _seed(t, automation=True, files_scope=False)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        job = OneDriveFolderJob(
            org_id=t.org.id, entity_type="company", entity_id=uuid.uuid4(), name="X"
        )
        session.add(job)
        await session.flush()
        await provision_folder(session, t.org, job)
        assert job.status == "skipped"
        assert job.last_error == "automation_connection_missing_files_scope"
        await session.commit()


async def test_state_and_panel_say_whether_provisioning_can_work(client_for) -> None:
    t = await make_tenant("od-state")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        state = (await c.get("/api/v1/microsoft/onedrive/state", headers=headers)).json()
        assert state == {
            "enabled": True,
            "viewer_connected": True,
            "can_provision": True,
            "job_status": None,
            "job_error": None,
        }
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            session.add(
                OneDriveFolderJob(
                    org_id=t.org.id,
                    entity_type="company",
                    entity_id=uuid.UUID(company["id"]),
                    name="Klant BV",
                    status="failed",
                    attempts=5,
                    last_error="403 accessDenied: nope",
                )
            )
            await session.commit()
        state = (
            await c.get(
                "/api/v1/microsoft/onedrive/state",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert state["job_status"] == "failed" and "accessDenied" in state["job_error"]

        # The company panel's provider reads the same flags, through the hub's own route.
        panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        ).json()
        panel = next(p for p in panels if p["key"] == "microsoft.onedrive.company")
        data = panel["data"]
        assert data["can_provision"] is True and data["can_pick"] is True
        assert data["can_manage"] is True and data["viewer_connected"] is True
        assert data["folder"] is None and data["links"] == []


async def test_bulk_provision_queues_only_folderless_companies(client_for, monkeypatch) -> None:
    async def _no_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _no_enqueue)
    t = await make_tenant("od-bulk")
    await _seed(t, automation=True)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        with_folder = await _company(c, headers, "Heeft map")
        await _company(c, headers, "Zonder map")
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            session.add(
                OneDriveLink(
                    org_id=t.org.id,
                    entity_type="company",
                    entity_id=uuid.UUID(with_folder["id"]),
                    drive_id="drv-1",
                    item_id="fold-1",
                    web_url="https://x",
                    name="Heeft map",
                    is_folder=True,
                    is_root=True,
                )
            )
            await session.commit()
        queued = await c.post("/api/v1/microsoft/onedrive/provision-all", headers=headers)
        assert queued.status_code == 200 and queued.json()["queued"] == 1
    jobs = await _queued_jobs(t.org.id)
    assert [job.name for job in jobs] == ["Zonder map"]
