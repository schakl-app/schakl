"""Changing a task in words, and writing its steps from its notes (``tasks/assist.py``).

Provider calls are faked at ``app.core.ai.providers.stream_chat`` — the one seam every AI
feature goes through — so these exercise the platform's own rules: the writes land as the
caller through ``TaskService`` (trail, validation, the ``:own`` rule), an id the model was
never shown is dropped, a link the instruction does not contain is dropped, and nothing here
can cross a tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.ai.providers import AIEvent, ToolCall
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.tasks.assist import SUBMIT_CHANGES, SUBMIT_CHECKLIST
from app.modules.tasks.models import TaskActivity
from tests.conftest import FAR_FUTURE_DUE, add_membership, auth_cookie, make_tenant, org_today

_password_hash = PasswordHash.recommended()

SETTINGS_BODY = {
    "provider": "anthropic",
    "api_key": "sk-test-super-secret-123",
    "features": {"task_assist": {"enabled": True}},
}


@pytest.fixture(autouse=True)
def _fresh_features_cache():
    from app.core.ai import service

    service._features_cache.clear()
    yield
    service._features_cache.clear()


def _fake_stream(events: list[AIEvent], seen: dict | None = None):
    async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
        if seen is not None:
            seen.update(kwargs)
        for event in events:
            yield event

    return fake


def _changes(**fields) -> list[AIEvent]:  # noqa: ANN003
    return [
        AIEvent(
            kind="tool_call",
            tool_call=ToolCall(id="c1", name=SUBMIT_CHANGES.name, input=dict(fields)),
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=5, tokens_out=9),
    ]


def _checklist(**fields) -> list[AIEvent]:  # noqa: ANN003
    return [
        AIEvent(
            kind="tool_call",
            tool_call=ToolCall(id="c1", name=SUBMIT_CHECKLIST.name, input=dict(fields)),
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=5, tokens_out=9),
    ]


async def _task_with_steps(c, headers) -> tuple[str, str, list[str]]:  # noqa: ANN001
    """A task, one checklist, two steps — the ids the answer is allowed to name."""
    created = await c.post(
        "/api/v1/tasks",
        json={
            "title": "Homepage opleveren",
            "description": "De klant wil een rustige uitstraling.",
            "due_date": FAR_FUTURE_DUE,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    checklist = await c.post(
        f"/api/v1/tasks/{task_id}/checklists", json={"title": "Aanpak"}, headers=headers
    )
    assert checklist.status_code == 201, checklist.text
    checklist_id = checklist.json()["id"]
    item_ids = []
    for title in ("Concept maken", "Teksten plaatsen"):
        item = await c.post(
            f"/api/v1/tasks/{task_id}/checklists/{checklist_id}/items",
            json={"title": title},
            headers=headers,
        )
        assert item.status_code == 201, item.text
        item_ids.append(item.json()["id"])
    return task_id, checklist_id, item_ids


async def test_revise_applies_every_kind_of_change_as_the_caller(client_for, monkeypatch) -> None:
    """Fields, steps in an existing list, steps in a new list, a rename, a tick, a removal, a
    link — and every one of them on the trail under the caller's own name."""
    t = await make_tenant("revise-all")
    headers = await auth_cookie(t.user)
    due = (org_today() + timedelta(days=5)).isoformat()
    seen: dict = {}
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        task_id, checklist_id, (first, second) = await _task_with_steps(c, headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _changes(
                    title="Homepage opleveren (v2)",
                    description="De klant wil een rustige uitstraling.\n\nIn het blauw.",
                    due_date=due,
                    priority="high",
                    requires_interaction=True,
                    add_items=[
                        {"checklist_id": checklist_id, "title": "DNS omzetten"},
                        {
                            "checklist_id": None,
                            "checklist_title": "Nazorg",
                            "title": "Klant bellen",
                            "description": "Na een week",
                        },
                        {"checklist_id": None, "checklist_title": "Nazorg", "title": "Factuur"},
                    ],
                    update_items=[{"item_id": first, "title": "Concept schetsen", "done": True}],
                    remove_item_ids=[second],
                    links=[{"url": "https://klant.nl/brief", "title": "Briefing"}],
                    summary="Stap DNS toegevoegd, deadline gezet, briefing gelinkt.",
                ),
                seen,
            ),
        )
        revised = await c.post(
            f"/api/v1/tasks/{task_id}/ai/revise",
            json={
                "instruction": (
                    f"voeg DNS omzetten toe, deadline {due}, prioriteit hoog, in het blauw, "
                    "link https://klant.nl/brief als briefing, nazorg lijst met bellen en factuur"
                )
            },
            headers=headers,
        )
        assert revised.status_code == 200, revised.text
        body = revised.json()
        assert body["summary"].startswith("Stap DNS")
        assert body["truncated"] is False
        assert set(body["changed"]) >= {
            "title",
            "description",
            "due_date",
            "priority",
            "requires_interaction",
            "checklist_items_added",
            "checklist_items_updated",
            "checklist_items_removed",
            "links",
        }
        task = body["task"]
        assert task["title"] == "Homepage opleveren (v2)"
        assert task["description"].endswith("In het blauw.")
        assert task["due_date"] == due
        assert task["priority"] == "high"
        assert task["requires_interaction"] is True
        lists = {cl["title"]: cl for cl in task["checklists"]}
        # The existing list: first step renamed and ticked, second removed, DNS added after.
        assert [(i["title"], i["done"]) for i in lists["Aanpak"]["items"]] == [
            ("Concept schetsen", True),
            ("DNS omzetten", False),
        ]
        # Two steps asked for one new list get one new list, not two.
        assert [i["title"] for i in lists["Nazorg"]["items"]] == ["Klant bellen", "Factuur"]
        assert lists["Nazorg"]["items"][0]["description"] == "Na een week"
        assert len(task["checklists"]) == 2
        assert [(link["url"], link["title"]) for link in task["links"]] == [
            ("https://klant.nl/brief", "Briefing")
        ]
        # The model saw the task as data with the ids it may name, and the instruction beside it.
        sent = seen["messages"][0].content
        assert checklist_id in sent and first in sent and "voeg DNS omzetten toe" in sent
        assert "point of view" in seen["system"]

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(
                select(TaskActivity.action, TaskActivity.actor_user_id, TaskActivity.payload)
                .where(TaskActivity.task_id == uuid.UUID(task_id))
                .order_by(TaskActivity.created_at)
            )
        ).all()
    actions = [r[0] for r in rows]
    # Every write is an ordinary write on the trail — a rename is a rename — plus one line
    # that says a model did it, under the person who asked.
    assert "checklist_item_renamed" in actions
    assert "checklist_item_completed" in actions
    assert "checklist_item_deleted" in actions
    assert "checklist_created" in actions
    assert "ai_revised" in actions
    ai_line = next(r for r in rows if r[0] == "ai_revised")
    assert ai_line[1] == t.user.id
    assert ai_line[2]["summary"].startswith("Stap DNS")
    assert all(r[1] == t.user.id for r in rows)


async def test_revise_drops_what_it_was_not_shown_and_what_the_instruction_did_not_say(
    client_for, monkeypatch
) -> None:
    """#129's rule, twice: an item id the model invented (or borrowed from another task) names
    nothing, and a link the instruction does not contain is not added — the one field whose
    value is worth forging."""
    t = await make_tenant("revise-ground")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        task_id, checklist_id, (first, _) = await _task_with_steps(c, headers)
        other_id, other_checklist, (other_item, _) = await _task_with_steps(c, headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _changes(
                    add_items=[{"checklist_id": other_checklist, "title": "Verdwaald"}],
                    update_items=[
                        {"item_id": other_item, "done": True},
                        {"item_id": str(uuid.uuid4()), "title": "Spook"},
                    ],
                    remove_item_ids=[other_item, first[:-1] + "0"],
                    links=[
                        {"url": "https://evil.example/login"},
                        {"url": "klant.nl/brief"},
                    ],
                    due_date="2099-01-01",
                    summary="…",
                )
            ),
        )
        revised = await c.post(
            f"/api/v1/tasks/{task_id}/ai/revise",
            json={"instruction": "zet de briefing klant.nl/brief erbij"},
            headers=headers,
        )
        assert revised.status_code == 200, revised.text
        body = revised.json()
        task = body["task"]
        # The stray step landed in *this* task's first list rather than in the other task's.
        assert [i["title"] for i in task["checklists"][0]["items"]] == [
            "Concept maken",
            "Teksten plaatsen",
            "Verdwaald",
        ]
        assert len(task["checklists"]) == 1
        # The typed host is completed and kept; the address nobody typed is gone.
        assert [link["url"] for link in task["links"]] == ["https://klant.nl/brief"]
        # A date off the end of the calendar is dropped, never stored.
        assert task["due_date"] == FAR_FUTURE_DUE
        assert "checklist_items_updated" not in body["changed"]
        assert "checklist_items_removed" not in body["changed"]

        other = await c.get(f"/api/v1/tasks/{other_id}", headers=headers)
        items = other.json()["checklists"][0]["items"]
        assert [(i["title"], i["done"]) for i in items] == [
            ("Concept maken", False),
            ("Teksten plaatsen", False),
        ]


async def test_revise_that_moves_a_deadline_later_carries_the_instruction_as_the_reason(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("revise-reason")
    headers = await auth_cookie(t.user)
    soon = (org_today() + timedelta(days=2)).isoformat()
    later = (org_today() + timedelta(days=9)).isoformat()
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post(
            "/api/v1/tasks", json={"title": "Iets", "due_date": soon}, headers=headers
        )
        task_id = created.json()["id"]
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(_changes(due_date=later, summary="Deadline verzet.")),
        )
        revised = await c.post(
            f"/api/v1/tasks/{task_id}/ai/revise",
            json={"instruction": "klant is op vakantie, een week later"},
            headers=headers,
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["task"]["due_date"] == later
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        payloads = (
            await session.execute(
                select(TaskActivity.payload).where(
                    TaskActivity.task_id == uuid.UUID(task_id),
                    TaskActivity.action == "due_extended",
                )
            )
        ).scalars().all()
    assert payloads and "vakantie" in str(payloads[0])


async def test_revise_is_gated_on_ai_use_and_on_the_task_write(client_for, monkeypatch) -> None:
    """The route is the task write it is; the service asks ``ai.use`` and the ``:own`` rule
    before a token is spent. A client-role login holds neither."""
    t = await make_tenant("revise-perm")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        reader = User(
            id=uuid.uuid4(),
            email="reader-revise@example.com",
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(reader)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, reader.id, role="client")
        await session.commit()
    reader_headers = await auth_cookie(reader, t.org.id)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat", _fake_stream(_changes(title="Nee"))
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        task_id, _, _ = await _task_with_steps(c, headers)
        refused = await c.post(
            f"/api/v1/tasks/{task_id}/ai/revise",
            json={"instruction": "hernoem"},
            headers=reader_headers,
        )
        assert refused.status_code == 403, refused.text
        # Feature off: the ordinary 409, and nothing written.
        await c.put(
            "/api/v1/ai/settings",
            json={**SETTINGS_BODY, "features": {"task_assist": {"enabled": False}}},
            headers=headers,
        )
        off = await c.post(
            f"/api/v1/tasks/{task_id}/ai/revise",
            json={"instruction": "hernoem"},
            headers=headers,
        )
        assert off.status_code == 409, off.text
        assert off.json()["error"]["code"] == "ai_feature_disabled"
        task = await c.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert task.json()["title"] == "Homepage opleveren"


async def test_revise_never_crosses_a_tenant(client_for, monkeypatch) -> None:
    t1 = await make_tenant("revise-t1")
    t2 = await make_tenant("revise-t2")
    h1 = await auth_cookie(t1.user)
    h2 = await auth_cookie(t2.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat", _fake_stream(_changes(title="Gekaapt"))
    )
    async with client_for(t1.host) as c1:
        await c1.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=h1)
        task_id, _, _ = await _task_with_steps(c1, h1)
    async with client_for(t2.host) as c2:
        await c2.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=h2)
        crossed = await c2.post(
            f"/api/v1/tasks/{task_id}/ai/revise", json={"instruction": "x"}, headers=h2
        )
        assert crossed.status_code in (403, 404), crossed.text
        crossed = await c2.post(
            f"/api/v1/tasks/{task_id}/ai/checklist", json={}, headers=h2
        )
        assert crossed.status_code in (403, 404), crossed.text
    async with client_for(t1.host) as c1:
        task = await c1.get(f"/api/v1/tasks/{task_id}", headers=h1)
        assert task.json()["title"] == "Homepage opleveren"


async def test_generate_checklist_writes_one_list_with_its_steps(client_for, monkeypatch) -> None:
    t = await make_tenant("revise-steps")
    headers = await auth_cookie(t.user)
    seen: dict = {}
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Webshop koppelen aan boekhouding",
                "description": "Orders moeten als factuur in SnelStart landen.",
                "due_date": FAR_FUTURE_DUE,
            },
            headers=headers,
        )
        task_id = created.json()["id"]
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _checklist(
                    title="Aanpak",
                    items=[
                        {"title": "Koppelsleutel opvragen"},
                        {"title": "Testorder plaatsen", "description": "Op de acceptatie"},
                        {"title": ""},
                        {"title": "Factuur controleren in SnelStart"},
                    ],
                ),
                seen,
            ),
        )
        generated = await c.post(
            f"/api/v1/tasks/{task_id}/ai/checklist",
            json={"instruction": "technische stappen"},
            headers=headers,
        )
        assert generated.status_code == 201, generated.text
        body = generated.json()
        assert body["title"] == "Aanpak"
        assert [i["title"] for i in body["items"]] == [
            "Koppelsleutel opvragen",
            "Testorder plaatsen",
            "Factuur controleren in SnelStart",
        ]
        assert body["items"][1]["description"] == "Op de acceptatie"
        assert "SnelStart" in seen["messages"][0].content
        assert "technische stappen" in seen["messages"][0].content
        assert "agency's staff" in seen["system"]

        task = await c.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert len(task.json()["checklists"]) == 1

        # A model that finds no steps is a 422 with its own key, and nothing is created.
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_checklist(title="Leeg", items=[]))
        )
        empty = await c.post(
            f"/api/v1/tasks/{task_id}/ai/checklist", json={}, headers=headers
        )
        assert empty.status_code == 422, empty.text
        assert empty.json()["error"]["code"] == "ai_empty_answer"
        task = await c.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert len(task.json()["checklists"]) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        actions = (
            await session.execute(
                select(TaskActivity.action).where(TaskActivity.task_id == uuid.UUID(task_id))
            )
        ).scalars().all()
    assert "checklist_created" in actions
    assert "ai_checklist" in actions
