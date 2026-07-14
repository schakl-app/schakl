"""Create-a-folder-while-browsing (#150 follow-up): POST /api/v1/google/drive/folders.

Acts as the viewing user (the stubbed ``acting_as``), name-matches before creating so a
re-typed name links instead of duplicating, and busts the viewer's browse cache.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_google_drive import (
    _seed,
    _stub_acting_as,
    _StubClient,
    _StubResponse,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def get(self, key: str):  # noqa: ANN001
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ANN001, ARG002
        return None

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_create_folder_makes_new_and_returns_it(client_for, monkeypatch) -> None:
    t = await make_tenant("gdrive-mkdir")
    await _seed(t)
    headers = await auth_cookie(t.user)
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.modules.google.drive.service.get_redis", lambda: fake_redis)

    # No name match under the parent → search then create.
    stub = _StubClient(
        [
            ("GET", _StubResponse(200, {"files": []})),
            (
                "POST",
                _StubResponse(
                    200,
                    {
                        "id": "new-9",
                        "name": "Offertes",
                        "webViewLink": "https://drive.google.com/drive/folders/new-9",
                    },
                ),
            ),
        ]
    )
    monkeypatch.setattr("app.modules.google.drive.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/google/drive/folders",
            json={"parent_id": "parent-1", "name": "Offertes"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body == {
            "id": "new-9",
            "name": "Offertes",
            "web_view_link": "https://drive.google.com/drive/folders/new-9",
        }
        assert stub.script == []  # both the search and the create ran
        # The create was parented on the folder being browsed.
        assert stub.call_kwargs[-1]["json"]["parents"] == ["parent-1"]
        # The parent's cached listing was invalidated so the new folder shows at once.
        assert any("parent-1" in key for key in fake_redis.deleted)


async def test_create_folder_links_existing_name_instead_of_duplicating(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("gdrive-mkdir-dup")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.modules.google.drive.service.get_redis", lambda: _FakeRedis())

    # A name match means the create POST never happens (the script offers only the search).
    stub = _StubClient(
        [
            (
                "GET",
                _StubResponse(
                    200,
                    {"files": [{"id": "exists-3", "name": "Offertes", "webViewLink": "https://d/x"}]},
                ),
            )
        ]
    )
    monkeypatch.setattr("app.modules.google.drive.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/google/drive/folders",
            json={"parent_id": "parent-1", "name": "Offertes"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["id"] == "exists-3"
        assert stub.script == []  # no create call was made


async def test_create_folder_rejects_blank_name(client_for, monkeypatch) -> None:
    t = await make_tenant("gdrive-mkdir-blank")
    await _seed(t)
    headers = await auth_cookie(t.user)
    monkeypatch.setattr("app.modules.google.drive.service.get_redis", lambda: _FakeRedis())
    async with client_for(t.host) as c:
        # A whitespace-only name is a 422 before any Google call (schema min_length is 1 char,
        # so a single space passes validation but the service rejects the empty trim).
        blank = await c.post(
            "/api/v1/google/drive/folders",
            json={"parent_id": "parent-1", "name": " "},
            headers=headers,
        )
        assert blank.status_code == 422, blank.text
