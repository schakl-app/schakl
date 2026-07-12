"""AI core (epic #131): settings round-trip, gating, budget, metering, tool isolation.

Provider calls are faked by monkeypatching ``app.core.ai.providers.stream_chat`` — the one
seam every feature goes through — so these tests exercise the platform's own behaviour
(§15 permissions, Golden Rule 1 isolation, the #126 non-negotiables) without network I/O.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.core.ai.models import AIUsage
from app.core.ai.providers import AIEvent, ToolCall
from app.core.ai.service import invalidate_features_cache
from app.core.ai.tools import available_tools, get_tool, run_tool
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.companies.models import Company
from tests.conftest import auth_cookie, make_tenant

SETTINGS_BODY = {
    "provider": "anthropic",
    "api_key": "sk-test-super-secret-123",
    "features": {"assistant": {"enabled": True}},
}


def _fake_stream(events: list[AIEvent]):
    async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
        for event in events:
            yield event

    return fake


@pytest.fixture(autouse=True)
def _fresh_features_cache():
    """The per-org features cache outlives the truncated database — clear it per test."""
    from app.core.ai import service

    service._features_cache.clear()
    yield
    service._features_cache.clear()


async def test_ai_settings_roundtrip_key_never_echoed(client_for) -> None:
    t = await make_tenant("ai-settings")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Nothing configured yet.
        empty = await c.get("/api/v1/ai/settings", headers=headers)
        assert empty.status_code == 200 and empty.json() is None

        saved = await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        # The key is write-only (#126 non-negotiable): a flag comes back, never the value.
        assert body["has_key"] is True
        assert "sk-test-super-secret-123" not in saved.text
        # The provider default fills an empty model; every feature has a config.
        assert body["default_model"]
        assert set(body["features"]) == {
            "assistant", "writing_assist", "time_assist", "reporting",
        }

        # An empty key on an update means "keep what is stored".
        again = await c.put(
            "/api/v1/ai/settings",
            json={"provider": "anthropic", "api_key": ""},
            headers=headers,
        )
        assert again.status_code == 200 and again.json()["has_key"] is True

        # A first save without a key is refused.
        removed = await c.delete("/api/v1/ai/settings", headers=headers)
        assert removed.status_code == 204
        invalidate_features_cache(t.org.id)
        no_key = await c.put(
            "/api/v1/ai/settings", json={"provider": "openai"}, headers=headers
        )
        assert no_key.status_code == 422

        # An OpenAI-compatible server needs its base_url.
        compat = await c.put(
            "/api/v1/ai/settings",
            json={"provider": "openai_compatible", "api_key": "k", "default_model": "m"},
            headers=headers,
        )
        assert compat.status_code == 422


async def test_ai_settings_tenant_isolation(client_for) -> None:
    a = await make_tenant("ai-iso-a")
    b = await make_tenant("ai-iso-b")
    async with client_for(a.host) as c:
        saved = await c.put(
            "/api/v1/ai/settings", json=SETTINGS_BODY, headers=await auth_cookie(a.user)
        )
        assert saved.status_code == 200
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        assert (await c.get("/api/v1/ai/settings", headers=headers)).json() is None
        usage = (await c.get("/api/v1/ai/usage", headers=headers)).json()
        assert usage["features"] == [] and usage["tokens_total"] == 0


async def test_ai_settings_requires_manage_permission(client_for) -> None:
    t = await make_tenant("ai-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/ai/settings", headers=headers)).status_code == 403
        refused = await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        assert refused.status_code == 403


async def test_ai_features_off_means_409(client_for) -> None:
    t = await make_tenant("ai-gates")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # No provider configured → the standard 409, never a provider call.
        off = await c.post(
            "/api/v1/ai/assist/write",
            json={"action": "improve", "text": "hallo"},
            headers=headers,
        )
        assert off.status_code == 409
        assert off.json()["error"]["code"] == "ai_not_configured"

        # Configured, but the feature toggled off (#126: per-feature enable).
        await c.put(
            "/api/v1/ai/settings",
            json={**SETTINGS_BODY, "features": {"writing_assist": {"enabled": False}}},
            headers=headers,
        )
        disabled = await c.post(
            "/api/v1/ai/assist/write",
            json={"action": "improve", "text": "hallo"},
            headers=headers,
        )
        assert disabled.status_code == 409
        assert disabled.json()["error"]["code"] == "ai_feature_disabled"


async def test_writing_assist_streams_and_meters(client_for, monkeypatch) -> None:
    t = await make_tenant("ai-write")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream(
            [
                AIEvent(kind="text", text="Beter "),
                AIEvent(kind="text", text="geschreven."),
                AIEvent(kind="done", stop_reason="end_turn", tokens_in=12, tokens_out=7),
            ]
        ),
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post(
            "/api/v1/ai/assist/write",
            json={"action": "improve", "text": "slecht geschreven"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "Beter " in response.text and "event: done" in response.text

        # Usage is metered — counts and labels only, never content (#126).
        usage = (await c.get("/api/v1/ai/usage", headers=headers)).json()
        row = next(r for r in usage["features"] if r["feature"] == "writing_assist")
        assert row["tokens_in"] == 12 and row["tokens_out"] == 7


async def test_budget_blocks_and_override_passes(client_for, monkeypatch) -> None:
    t = await make_tenant("ai-budget")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream([AIEvent(kind="text", text="ok"), AIEvent(kind="done")]),
    )
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/ai/settings",
            json={**SETTINGS_BODY, "monthly_token_budget": 10},
            headers=headers,
        )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            AIUsage(
                org_id=t.org.id, user_id=t.user.id, feature="assistant",
                model="m", tokens_in=6, tokens_out=6,
            )
        )
        await session.commit()
    async with client_for(t.host) as c:
        blocked = await c.post(
            "/api/v1/ai/assist/write",
            json={"action": "improve", "text": "x"},
            headers=headers,
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "ai_budget_reached"

        # Interactive use sits behind an explicit acknowledgement, never a silent pass (#126).
        allowed = await c.post(
            "/api/v1/ai/assist/write",
            json={"action": "improve", "text": "x", "override_budget": True},
            headers=headers,
        )
        assert allowed.status_code == 200 and "ok" in allowed.text


async def test_time_parse_drops_ungrounded_ids(client_for, monkeypatch) -> None:
    """#129: an ID the find tools never returned is dropped, not guessed."""
    t = await make_tenant("ai-parse")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream(
            [
                AIEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="submit_time_entry",
                        input={
                            "date": "2026-07-10",
                            "start": "14:00",
                            "end": "16:30",
                            # Never appeared in any tool result → must come back null.
                            "company_id": "0a95cd21-9d3e-4b41-b6ec-2b9dbb5ff0aa",
                            "description": "homepage overleg",
                        },
                    ),
                ),
                AIEvent(kind="done", stop_reason="tool_use", tokens_in=3, tokens_out=3),
            ]
        ),
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        parsed = await c.post(
            "/api/v1/ai/time/parse",
            json={"text": "gisteren 14:00-16:30 website Jansen, homepage overleg"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["date"] == "2026-07-10"
        assert body["start"] == "14:00" and body["end"] == "16:30"
        assert body["company_id"] is None
        assert body["description"] == "homepage overleg"


async def test_tool_layer_tenant_isolation() -> None:
    """#127 acceptance: org A's tools can never see org B's rows — the handlers run the
    same tenant-scoped services, and the RLS GUC backs them."""
    a = await make_tenant("ai-tool-a")
    b = await make_tenant("ai-tool-b")
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        session.add(Company(org_id=a.org.id, name="Alpha Geheim BV"))
        await session.commit()

    perms = PermissionSet.of(["companies.company.read"])
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        ctx_a = RequestContext(user=a.user, org=a.org, session=session, permissions=perms)
        spec = get_tool(ctx_a, "companies.find")
        assert spec is not None
        mine = await run_tool(ctx_a, spec, {"query": "Alpha"})
        assert any("Alpha Geheim" in c["name"] for c in mine.data["companies"])

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        ctx_b = RequestContext(user=b.user, org=b.org, session=session, permissions=perms)
        theirs = await run_tool(ctx_b, get_tool(ctx_b, "companies.find"), {"query": "Alpha"})
        assert theirs.data["companies"] == []


async def test_tools_filtered_by_permission() -> None:
    t = await make_tenant("ai-tool-perm")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        nothing = RequestContext(
            user=t.user, org=t.org, session=session, permissions=PermissionSet.of([])
        )
        assert available_tools(nothing) == []
        reader = RequestContext(
            user=t.user, org=t.org, session=session,
            permissions=PermissionSet.of(["companies.company.read"]),
        )
        names = {spec.name for spec in available_tools(reader)}
        assert "companies.find" in names
        assert all(name.startswith("companies.") for name in names)


async def test_me_carries_enabled_ai_features(client_for) -> None:
    t = await make_tenant("ai-meta")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        before = await c.get("/api/v1/meta/me", headers=headers)
        assert before.json()["ai_features"] == []
        await c.put(
            "/api/v1/ai/settings",
            json={**SETTINGS_BODY, "features": {"assistant": {"enabled": False}}},
            headers=headers,
        )
        after = await c.get("/api/v1/meta/me", headers=headers)
        features = after.json()["ai_features"]
        assert "assistant" not in features
        assert "writing_assist" in features


async def test_reports_crud_and_isolation(client_for) -> None:
    a = await make_tenant("ai-rep-a")
    b = await make_tenant("ai-rep-b")
    a_headers = await auth_cookie(a.user)
    async with client_for(a.host) as c:
        created = await c.post(
            "/api/v1/ai/reports",
            json={
                "company_id": "0a95cd21-9d3e-4b41-b6ec-2b9dbb5ff0aa",
                "period": "2026-06",
                "language": "nl",
                "title": "Maandrapport juni",
                "content": "## Inleiding\nAlles goed.",
            },
            headers=a_headers,
        )
        assert created.status_code == 201, created.text
        report_id = created.json()["id"]
        assert created.json()["created_by_name"]

        listed = await c.get("/api/v1/ai/reports", headers=a_headers)
        assert [r["id"] for r in listed.json()] == [report_id]

        updated = await c.put(
            f"/api/v1/ai/reports/{report_id}",
            json={"title": "Maandrapport juni v2"},
            headers=a_headers,
        )
        assert updated.status_code == 200 and updated.json()["title"] == "Maandrapport juni v2"

    async with client_for(b.host) as c:
        b_headers = await auth_cookie(b.user)
        assert (await c.get("/api/v1/ai/reports", headers=b_headers)).json() == []
        foreign = await c.get(f"/api/v1/ai/reports/{report_id}", headers=b_headers)
        assert foreign.status_code == 404

    async with client_for(a.host) as c:
        assert (
            await c.delete(f"/api/v1/ai/reports/{report_id}", headers=a_headers)
        ).status_code == 204


async def test_models_listing_uses_typed_or_stored_key(client_for, monkeypatch) -> None:
    """#126 follow-up: the model picker is fetched live, with the same key semantics as
    save — a typed key works before anything is stored, an empty one falls back to the
    stored key, and a stored key is never sent to a *different* provider."""
    captured: list[tuple[str, str]] = []

    async def fake_list(config):  # noqa: ANN001
        captured.append((config.provider, config.api_key))
        return ["claude-opus-4-8", "claude-sonnet-5"]

    monkeypatch.setattr("app.core.ai.providers.list_models", fake_list)
    t = await make_tenant("ai-models")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Nothing stored, nothing typed → data-shaped error, never a 500.
        empty = await c.post("/api/v1/ai/settings/models", json={}, headers=headers)
        assert empty.status_code == 200 and empty.json()["error"]

        # First setup: a typed key works before saving.
        typed = await c.post(
            "/api/v1/ai/settings/models",
            json={"provider": "anthropic", "api_key": "sk-typed"},
            headers=headers,
        )
        assert typed.json()["models"] == ["claude-opus-4-8", "claude-sonnet-5"]

        # Stored key is reused without ever being played back…
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        stored = await c.post("/api/v1/ai/settings/models", json={}, headers=headers)
        assert stored.json()["models"] and stored.json()["error"] is None

        # …but never handed to another provider.
        other = await c.post(
            "/api/v1/ai/settings/models", json={"provider": "openai"}, headers=headers
        )
        assert other.json()["models"] == [] and other.json()["error"]

    assert captured == [
        ("anthropic", "sk-typed"),
        ("anthropic", SETTINGS_BODY["api_key"]),
    ]
