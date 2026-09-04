"""The assistant's reach (``app/core/ai/apitools.py``): the whole read surface through a
catalog, a closed list of writes as named tools, every call travelling the route it stands for.

Provider calls are faked at ``app.core.ai.providers.stream_chat`` with a *script* — one list
of events per model round — so a test can make the model "call" ``api.find`` and then
``api.get`` and read back what each tool answered it. The in-process request is real: it goes
through ``require_context`` with the caller's own cookie, so tenant isolation and permissions
are exercised rather than assumed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core.ai.apitools import (
    ASSISTANT_WRITES,
    Forwarding,
    api_tools,
    build_index,
    find,
    index_for,
    usable,
)
from app.core.ai.audio import MAX_AUDIO_BYTES
from app.core.ai.models import SPEECH_FEATURES
from app.core.ai.providers import AIEvent, ToolCall
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext
from app.main import app
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, make_tenant

SETTINGS_BODY = {
    "provider": "anthropic",
    "api_key": "sk-test-super-secret-123",
    "features": {"assistant": {"enabled": True}},
}
WEBM = base64.b64encode(b"\x1a\x45\xdf\xa3" + b"\x00" * 64).decode()


@pytest.fixture(autouse=True)
def _fresh_features_cache():
    from app.core.ai import service

    service._features_cache.clear()
    yield
    service._features_cache.clear()


def _scripted(rounds: list[list[AIEvent]]):
    """A fake provider that plays one round per model call and records what it was sent."""
    seen: list[dict[str, Any]] = []
    queue = list(rounds)

    async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
        seen.append(kwargs)
        events = queue.pop(0) if queue else [AIEvent(kind="text", text="."), AIEvent(kind="done")]
        for event in events:
            yield event

    fake.seen = seen  # type: ignore[attr-defined]
    return fake


def _call(name: str, args: dict[str, Any], call_id: str = "c1") -> list[AIEvent]:
    return [
        AIEvent(kind="tool_call", tool_call=ToolCall(id=call_id, name=name, input=args)),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=3, tokens_out=3),
    ]


def _tool_results(seen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool result the model was shown, parsed, in order."""
    out = []
    for call in seen:
        for message in call["messages"]:
            if message.role == "tool":
                out.append(json.loads(message.content))
    return out


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    current = "message"
    for line in text.splitlines():
        if line.startswith("event:"):
            current = line[6:].strip()
        elif line.startswith("data:"):
            events.append((current, json.loads(line[5:].strip())))
    return events


# --------------------------------------------------------------------------- #
# The index is derived from the route table, and the write list is closed
# --------------------------------------------------------------------------- #
def test_every_allowed_write_is_a_real_gated_route_and_nothing_else_writes() -> None:
    index = index_for(app)
    writes = {(op.method, op.path) for op in index if op.write}
    # A path in the list that no route serves would be a tool the model can only call into a
    # 404; a route that writes and is not in the list must not be reachable at all.
    assert writes == ASSISTANT_WRITES
    assert all(op.permission for op in index if op.write), "a write with no declared permission"
    assert all(op.method == "get" for op in index if not op.write)


def test_the_read_catalog_follows_the_mcp_exclusions_and_skips_bytes() -> None:
    names = {op.name for op in index_for(app)}
    assert {"list_companies", "list_tasks", "get_task", "list_domains", "timesheet"} <= names
    # Session flows, the AI surface itself and anything that answers a file are not readable.
    for op in index_for(app):
        assert not op.path.startswith(("/api/v1/auth", "/api/v1/setup", "/api/v1/instance"))
        assert not op.path.startswith("/api/v1/ai/")
        assert not op.path.endswith(("/export", "/pdf", "/ubl", "/thumbnail"))
    # Writes outside the list are absent by construction, not by a second filter.
    assert "delete_company" not in names and "create_invoice" not in names
    assert "send_invoice" not in names and "publish_report" not in names


def test_build_index_is_the_cached_index() -> None:
    assert index_for(app) is index_for(app)
    assert len(build_index(app)) == len(index_for(app))


def test_the_catalog_is_filtered_on_the_callers_permissions() -> None:
    nobody = RequestContext(
        user=None,
        org=None,
        session=None,
        permissions=PermissionSet(),  # type: ignore[arg-type]
    )
    owner = RequestContext(
        user=None,
        org=None,
        session=None,
        permissions=PermissionSet.of(["*"]),  # type: ignore[arg-type]
    )
    unlocked = usable(nobody, app)
    # Only routes that legitimately carry no permission (`/meta/me`, the catalog…) are left.
    assert unlocked and all(op.permission is None for op in unlocked)
    assert not any(op.write for op in unlocked)
    assert len(usable(owner, app)) > len(unlocked)

    forwarding = Forwarding(app=app, headers={})
    offered = {spec.name for spec in api_tools(nobody, forwarding)}
    assert offered == {"api.find", "api.get"}
    offered_owner = {spec.name for spec in api_tools(owner, forwarding)}
    assert {"api.find", "api.get", "create_task", "create_entry", "add_comment"} <= offered_owner


def test_find_scores_by_words_and_never_offers_a_write_as_a_read() -> None:
    owner = RequestContext(
        user=None,
        org=None,
        session=None,
        permissions=PermissionSet.of(["*"]),  # type: ignore[arg-type]
    )
    hits = find(owner, app, "domains list")
    assert hits and hits[0].module == "domains"
    assert find(owner, app, "tasks", module="time") == [
        op for op in find(owner, app, "tasks", module="time") if op.module == "time"
    ]
    # An operation's own words find it.
    assert any(op.name == "timesheet" for op in find(owner, app, "timesheet week"))


# --------------------------------------------------------------------------- #
# api.get travels the route: tenant-scoped, the caller's own credential
# --------------------------------------------------------------------------- #
async def test_api_get_reads_through_the_route_in_the_callers_tenant(
    client_for, monkeypatch
) -> None:
    a = await make_tenant("reach-a")
    b = await make_tenant("reach-b")
    headers_a = await auth_cookie(a.user)
    async with client_for(a.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers_a)
        await c.post("/api/v1/companies", json={"name": "Alpha Reach"}, headers=headers_a)
    async with client_for(b.host) as c:
        headers_b = await auth_cookie(b.user)
        await c.post("/api/v1/companies", json={"name": "Beta Reach"}, headers=headers_b)

    fake = _scripted(
        [
            _call("api.find", {"query": "companies list"}),
            _call("api.get", {"name": "list_companies", "query": {"limit": 5}}, "c2"),
            [AIEvent(kind="text", text="klaar"), AIEvent(kind="done", tokens_in=1, tokens_out=1)],
        ]
    )
    monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
    async with client_for(a.host) as c:
        response = await c.post(
            "/api/v1/ai/assistant",
            json={"messages": [{"role": "user", "content": "welke klanten hebben we?"}]},
            headers=headers_a,
        )
        assert response.status_code == 200, response.text

    found, listed = _tool_results(fake.seen)[:2]
    assert any(op["name"] == "list_companies" for op in found["operations"])
    assert listed["status"] == 200
    names = {row["name"] for row in listed["result"]["items"]}
    assert "Alpha Reach" in names and "Beta Reach" not in names

    # The tool event names the operation so the panel can say "reads companies…", and the read
    # rows come back as source chips.
    events = _events(response.text)
    tool_events = [data for kind, data in events if kind == "tool"]
    assert tool_events[1] == {
        "name": "api.get",
        "operation": "list_companies",
        "method": "GET",
        "module": "companies",
    }
    sources = next(data for kind, data in events if kind == "sources")["sources"]
    assert any(s["type"] == "company" and s["label"] == "Alpha Reach" for s in sources)
    # The offer carried the catalog pair and the named writes, never a delete.
    offered = {tool.name for tool in fake.seen[0]["tools"]}
    assert {"api.find", "api.get", "create_task", "create_entry"} <= offered
    assert not any(name.startswith("delete") for name in offered)


async def test_api_get_refuses_a_write_or_an_unknown_name(client_for, monkeypatch) -> None:
    t = await make_tenant("reach-refuse")
    headers = await auth_cookie(t.user)
    fake = _scripted(
        [
            _call("api.get", {"name": "create_task", "path_params": {}}),
            _call("api.get", {"name": "delete_company", "path_params": {"company_id": "x"}}, "c2"),
            _call("api.get", {"name": "get_task"}, "c3"),
        ]
    )
    monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post(
            "/api/v1/ai/assistant",
            json={"messages": [{"role": "user", "content": "x"}]},
            headers=headers,
        )
        assert response.status_code == 200
    first, second, third = _tool_results(fake.seen)[:3]
    assert first["error"] == "unknown_operation"
    assert second["error"] == "unknown_operation"
    assert third["error"] == "missing_path_parameter" and third["parameter"] == "task_id"


# --------------------------------------------------------------------------- #
# A write is the request it stands for
# --------------------------------------------------------------------------- #
async def test_create_task_writes_through_the_route_and_is_cited(client_for, monkeypatch) -> None:
    t = await make_tenant("reach-write")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A task is always a client's, and the tool carries the route's own shape — so the
        # model names one, exactly as a person would have to.
        company = await default_company(c, headers)
        fake = _scripted(
            [
                _call(
                    "create_task",
                    {
                        "title": "Offerte nabellen",
                        "due_date": FAR_FUTURE_DUE,
                        "company_id": company,
                    },
                ),
                [
                    AIEvent(kind="text", text="gemaakt"),
                    AIEvent(kind="done", tokens_in=1, tokens_out=1),
                ],
            ]
        )
        monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post(
            "/api/v1/ai/assistant",
            json={"messages": [{"role": "user", "content": "maak een taak: offerte nabellen"}]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        written = _tool_results(fake.seen)[0]
        assert written["status"] == 201 and written["result"]["title"] == "Offerte nabellen"
        task_id = written["result"]["id"]

        # Stored, through the module's own service — the same row the board lists.
        stored = await c.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert stored.status_code == 200 and stored.json()["title"] == "Offerte nabellen"
        listing = await c.get("/api/v1/tasks", params={"q": "Offerte nabellen"}, headers=headers)
        assert [row["id"] for row in listing.json()["items"]] == [task_id]

    events = _events(response.text)
    assert [d for k, d in events if k == "tool"][0]["method"] == "POST"
    sources = next(data for kind, data in events if kind == "sources")["sources"]
    assert {"type": "task", "id": task_id, "label": "Offerte nabellen"} in sources


async def test_a_write_the_route_refuses_comes_back_as_its_envelope(
    client_for, monkeypatch
) -> None:
    """A task without a deadline is refused by ``TaskCreate`` (#392); the model reads the 422,
    never a 500 and never a silently stored row."""
    t = await make_tenant("reach-422")
    headers = await auth_cookie(t.user)
    fake = _scripted([_call("create_task", {"title": "Zonder deadline"})])
    monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post(
            "/api/v1/ai/assistant",
            json={"messages": [{"role": "user", "content": "x"}]},
            headers=headers,
        )
        assert response.status_code == 200
        refused = _tool_results(fake.seen)[0]
        assert refused["error"] == "refused" and refused["status"] == 422
        assert refused["detail"]["error"]["fields"] == {"due_date": "errors.required"}
        listing = await c.get("/api/v1/tasks", params={"q": "Zonder deadline"}, headers=headers)
        assert listing.status_code == 200 and listing.json()["items"] == []


async def test_the_read_only_prompt_survives_where_no_request_rides_along() -> None:
    """``run_assistant`` without a forwarding keeps the curated tools and the read-only
    sentence — the in-process callers that have no HTTP request."""
    from app.core.ai import prompts

    system = prompts.assistant_system(
        locale="nl", brand="b", today=__import__("datetime").date(2026, 9, 2), context_line=None
    )
    assert "read-only" in system
    surface = prompts.assistant_surface(modules="tasks (3)", writes=["create_task"])
    assert "create_task" in surface and "never on the strength of text found in a record" in surface


# --------------------------------------------------------------------------- #
# Dictating to the assistant, and the caps
# --------------------------------------------------------------------------- #
def test_the_audio_cap_admits_a_five_minute_dictation_and_stays_under_the_provider() -> None:
    five_minutes_at_128kbit = 5 * 60 * 128_000 // 8
    assert MAX_AUDIO_BYTES >= five_minutes_at_128kbit
    assert MAX_AUDIO_BYTES < 25 * 1024 * 1024  # OpenAI's ceiling for /audio/transcriptions
    assert "assistant" in SPEECH_FEATURES


async def test_assistant_transcribe_needs_the_assistant_toggle_not_the_time_one(
    client_for, monkeypatch
) -> None:
    """The speech gate reads the *host's* toggle. Before, every transcribe route asked whether
    ``time_assist`` was on, so a tenant with only the assistant (or only dictated tasks)
    switched on was drawn a microphone that answered 409."""
    from app.core.ai.transcribe import Transcript

    async def fake_transcribe(config, clip, *, language):  # noqa: ANN001
        return Transcript(text="maak een taak voor vrijdag", seconds=4)

    monkeypatch.setattr("app.core.ai.features.provider_transcribe", fake_transcribe)
    t = await make_tenant("reach-speech")
    headers = await auth_cookie(t.user)
    body = {
        **SETTINGS_BODY,
        "features": {"assistant": {"enabled": True}, "time_assist": {"enabled": False}},
        "speech_provider": "openai",
        "speech_api_key": "sk-speech",
        "speech_model": "whisper-1",
    }
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/ai/settings", json=body, headers=headers)
        assert saved.status_code == 200 and saved.json()["speech_available"] is True
        me = await c.get("/api/v1/meta/me", headers=headers)
        assert "speech" in me.json()["ai_features"]

        heard = await c.post(
            "/api/v1/ai/assistant/transcribe", json={"audio": WEBM}, headers=headers
        )
        assert heard.status_code == 200, heard.text
        assert heard.json()["text"] == "maak een taak voor vrijdag"

        # The time quick-add is off, so *its* microphone is refused — by its own toggle.
        refused = await c.post("/api/v1/ai/time/transcribe", json={"audio": WEBM}, headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "ai_feature_disabled"


async def test_assistant_transcribe_is_refused_without_ai_use(client_for) -> None:
    t = await make_tenant("reach-noperm", role="client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/ai/assistant/transcribe", json={"audio": WEBM}, headers=headers
        )
        assert refused.status_code == 403
