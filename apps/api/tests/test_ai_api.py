"""AI core (epic #131): settings round-trip, gating, budget, metering, tool isolation.

Provider calls are faked by monkeypatching ``app.core.ai.providers.stream_chat`` — the one
seam every feature goes through — so these tests exercise the platform's own behaviour
(§15 permissions, Golden Rule 1 isolation, the #126 non-negotiables) without network I/O.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

import pytest
from pwdlib import PasswordHash
from sqlalchemy import select, text

from app.core.ai.audio import MAX_ENCODED_CHARS
from app.core.ai.models import AIUsage
from app.core.ai.providers import AIEvent, ToolCall
from app.core.ai.service import invalidate_features_cache
from app.core.ai.tools import available_tools, get_tool, run_tool
from app.core.auth.models import User
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.companies.models import Company
from tests.conftest import add_membership, auth_cookie, make_tenant

_password_hash = PasswordHash.recommended()

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


def _submit(**fields) -> list[AIEvent]:
    """One forced `submit_time_entry` call — the shape the parse loop now expects in a single
    round (#246)."""
    return [
        AIEvent(
            kind="tool_call",
            tool_call=ToolCall(id="c1", name="submit_time_entry", input=dict(fields)),
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=3, tokens_out=3),
    ]


async def test_time_parse_grounds_on_the_prefetched_shortlist(client_for, monkeypatch) -> None:
    """#246: a client resolved from the candidate shortlist survives, because the shortlist is
    evidence exactly as a tool result is.

    This is the counterpart of ``test_time_parse_drops_ungrounded_ids`` above and the reason it
    still passes: grounding was *widened*, never relaxed. Miss this union and every correctly
    chosen id comes back null — a 200 that looks like the model has no tools at all.
    """
    t = await make_tenant("ai-parse-shortlist")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post("/api/v1/companies", json={"name": "Jansen"}, headers=headers)
        assert created.status_code == 201, created.text
        company_id = created.json()["id"]

        # The model is never given a tool result — only the shortlist the server prefetched.
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(_submit(company_id=company_id, description="homepage review")),
        )
        parsed = await c.post(
            "/api/v1/ai/time/parse",
            json={"text": "2 uur Jansen, homepage review"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["company_id"] == company_id


async def test_time_parse_fills_type_billable_and_break(client_for, monkeypatch) -> None:
    """#246: the three fields the output tool could not express before."""
    t = await make_tenant("ai-parse-fields")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        # Seeds the org's default entry types (`work`, `email`) — the vocabulary the parse
        # validates against.
        types = await c.get("/api/v1/time/entry-types", headers=headers)
        assert types.status_code == 200, types.text
        assert "work" in {row["key"] for row in types.json()}

        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _submit(
                    start="09:00",
                    end="17:00",
                    entry_type_key="work",
                    billable=False,
                    break_minutes=30,
                )
            ),
        )
        parsed = await c.post(
            "/api/v1/ai/time/parse",
            json={"text": "9-17 niet declarabel, half uur pauze"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["entry_type_key"] == "work"
        assert body["billable"] is False
        assert body["break_minutes"] == 30


async def test_time_parse_leaves_unstated_fields_null(client_for, monkeypatch) -> None:
    """#246 + #284: silence is not `false`.

    ``billable`` is tri-state on purpose — the form reads a non-null value as "the user decided"
    and stops applying the project's own default. A parse that always answered would quietly
    make every AI-drafted entry billable.
    """
    t = await make_tenant("ai-parse-tristate")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream(_submit(start="09:00", end="10:00", description="werk")),
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        parsed = await c.post(
            "/api/v1/ai/time/parse", json={"text": "9-10 werk"}, headers=headers
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["billable"] is None
        assert body["break_minutes"] is None
        assert body["entry_type_key"] is None


async def test_time_parse_drops_unknown_entry_type(client_for, monkeypatch) -> None:
    """An entry type is a tenant-defined slug, so membership in the org's own keys is its
    grounding. An invented key is dropped here rather than 422'd at the write."""
    t = await make_tenant("ai-parse-type")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream(_submit(start="09:00", end="10:00", entry_type_key="niet_bestaand")),
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        parsed = await c.post(
            "/api/v1/ai/time/parse", json={"text": "9-10 iets"}, headers=headers
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["entry_type_key"] is None


async def test_time_parse_honours_the_caller_s_today(client_for, monkeypatch) -> None:
    """The day the user is looking at is the day relative dates resolve against (#246).

    Without it the server answers with its own today, and the client then navigates the user
    off the day they were working on.
    """
    t = await make_tenant("ai-parse-today")
    headers = await auth_cookie(t.user)
    seen: dict[str, str] = {}

    async def capture(config, **kwargs):  # noqa: ANN001, ANN003
        seen["system"] = kwargs["system"]
        for event in _submit(start="14:00", end="16:00"):
            yield event

    monkeypatch.setattr("app.core.ai.providers.stream_chat", capture)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        parsed = await c.post(
            "/api/v1/ai/time/parse",
            json={"text": "vanmiddag 14:00-16:00", "today": "2026-03-17"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
    assert "2026-03-17" in seen["system"]


async def test_time_parse_query_budget(client_for, monkeypatch, count_queries) -> None:
    """The parse is a fixed number of statements, and adding clients does not move it (#246).

    The shape this pins is invisible in the JSON: the old loop re-read the settings row and
    re-summed the month's usage on *every* model round, so the cost scaled with how many tool
    round trips the model happened to take. Both versions return the same body.
    """
    t = await make_tenant("ai-parse-budget")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream(_submit(start="09:00", end="10:00")),
    )
    async with client_for(t.host) as c:
        # A budget is set on purpose: without one `ensure_budget` short-circuits before its
        # month-wide SUM, and the per-round re-gating this pins would cost nothing to measure.
        await c.put(
            "/api/v1/ai/settings",
            json={**SETTINGS_BODY, "monthly_token_budget": 1_000_000},
            headers=headers,
        )
        for name in ("Alpha", "Beta", "Gamma"):
            await c.post("/api/v1/companies", json={"name": name}, headers=headers)

        # One warm-up: the first typed read of the org seeds its default entry types (#176),
        # a one-time write that is not part of the steady-state shape being pinned here.
        warm = await c.post(
            "/api/v1/ai/time/parse", json={"text": "9-10 Alpha"}, headers=headers
        )
        assert warm.status_code == 200, warm.text

        with count_queries() as first:
            res = await c.post(
                "/api/v1/ai/time/parse", json={"text": "9-10 Alpha"}, headers=headers
            )
            assert res.status_code == 200, res.text

        for name in ("Delta", "Epsilon", "Zeta", "Eta"):
            await c.post("/api/v1/companies", json={"name": name}, headers=headers)

        with count_queries() as second:
            res = await c.post(
                "/api/v1/ai/time/parse", json={"text": "9-10 Alpha"}, headers=headers
            )
            assert res.status_code == 200, res.text

    # Flat in the number of clients — the shortlist is one capped query, not one per row.
    assert len(second.statements) == len(first.statements), (
        f"parse scales with row count: {len(first.statements)} → {len(second.statements)}"
    )
    # And bounded in absolute terms (15 today), so re-introducing the per-round settings read
    # and month-sum — three statements per extra round — trips this rather than going unnoticed.
    assert len(first.statements) <= 16, (
        f"parse issues {len(first.statements)} statements:\n"
        + "\n".join(first.statements)
    )


# --------------------------------------------------------------------------- #
# Speech to text (#246)
# --------------------------------------------------------------------------- #
#: A minimal clip whose first bytes are a real WebM/EBML header — the format is sniffed from
#: content, so a plausible header is all the validator needs.
WEBM = base64.b64encode(b"\x1a\x45\xdf\xa3" + b"\x00" * 64).decode()

SPEECH_BODY = {
    **SETTINGS_BODY,
    "speech_provider": "openai",
    "speech_api_key": "sk-speech-secret-456",
    "speech_model": "whisper-1",
}


def _fake_transcript(text: str, seconds: int = 12):
    async def fake(config, clip, *, language):  # noqa: ANN001, ANN003
        from app.core.ai.transcribe import Transcript

        return Transcript(text=text, seconds=seconds)

    return fake


async def test_transcribe_needs_its_own_speech_provider(client_for) -> None:
    """Anthropic has no speech endpoint and is the default provider, so "reuse the chat
    credential" resolves to nothing for the typical tenant (#246).

    The answer is the ordinary 409, not a 500 — and `speech_available` is false, which is what
    keeps the web app from drawing a microphone it would then have to apologise for.
    """
    t = await make_tenant("ai-speech-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["speech_available"] is False

        refused = await c.post(
            "/api/v1/ai/time/transcribe", json={"audio": WEBM}, headers=headers
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "ai_speech_not_configured"


async def test_speech_capability_is_reported_only_when_it_can_work(client_for) -> None:
    """"Off means invisible" (#126) applied to dictation.

    `/meta/me` carries the AI capability list the web app gates on. Without `speech` in it, an
    Anthropic-configured org — the default — would be drawn a microphone that 409s on the first
    click, which is exactly the shape this rule exists to prevent.
    """
    t = await make_tenant("ai-speech-flag")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        invalidate_features_cache(t.org.id)
        me = await c.get("/api/v1/meta/me", headers=headers)
        assert me.status_code == 200, me.text
        assert "time_assist" in me.json()["ai_features"]
        assert "speech" not in me.json()["ai_features"]

        await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=headers)
        invalidate_features_cache(t.org.id)
        me = await c.get("/api/v1/meta/me", headers=headers)
        assert "speech" in me.json()["ai_features"]

        # Turning time assist off takes dictation with it — it has no separate toggle.
        await c.put(
            "/api/v1/ai/settings",
            json={**SPEECH_BODY, "features": {"time_assist": {"enabled": False}}},
            headers=headers,
        )
        invalidate_features_cache(t.org.id)
        assert "speech" not in (await c.get("/api/v1/meta/me", headers=headers)).json()[
            "ai_features"
        ]


async def test_transcribe_roundtrip_and_meters_seconds(client_for, monkeypatch) -> None:
    """The transcript comes back as text and the cost is metered in seconds (#246).

    Seconds are their own column: folding them into `tokens_out` would inflate the number the
    monthly *token* budget is enforced against.
    """
    t = await make_tenant("ai-speech-on")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.features.provider_transcribe", _fake_transcript("twee uur Jansen", 14)
    )
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=headers)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["speech_available"] is True
        assert body["has_speech_key"] is True
        # Write-only, exactly like the chat key (#126).
        assert "sk-speech-secret-456" not in saved.text

        res = await c.post(
            "/api/v1/ai/time/transcribe",
            json={"audio": WEBM, "language": "nl-NL"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["text"] == "twee uur Jansen"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(AIUsage).where(AIUsage.org_id == t.org.id))).scalars()
        audio_rows = [r for r in rows if r.audio_seconds]
        assert len(audio_rows) == 1
        assert audio_rows[0].audio_seconds == 14
        assert audio_rows[0].tokens_in == 0 and audio_rows[0].tokens_out == 0


async def test_transcribe_requires_writing_time_entries(client_for, monkeypatch) -> None:
    """A transcript exists to become a time entry, so holding `ai.use` alone is not enough.

    The route's declared permission is what makes the surface enumerable (§15); this is the
    second half of the same rule, and it is the half a read-only member hits.
    """
    t = await make_tenant("ai-speech-perm")
    owner_headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.features.provider_transcribe", _fake_transcript("iets")
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=owner_headers)

    # A member holds `ai.use` *and* `time.entry.write:own` by default, so take the write away
    # to isolate the rule under test: AI access alone must not reach this. (The owner holds
    # `*` and is deliberately not the subject here — a wildcard would mask the check.)
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email="ai-speech-reader@example.com",
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, user.id, "member")
        await session.execute(
            text(
                "DELETE FROM role_permissions WHERE org_id = :org "
                "AND permission LIKE 'time.entry.write%'"
            ),
            {"org": t.org.id},
        )
        await session.commit()
        member = User(id=user.id, email=user.email, hashed_password="", is_active=True)

    member_headers = await auth_cookie(member)
    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/ai/time/transcribe", json={"audio": WEBM}, headers=member_headers
        )
        assert refused.status_code == 403, refused.text


async def test_transcribe_rejects_oversized_and_unknown_audio(client_for) -> None:
    """Every cap is checked before the work it bounds, and over a limit is an error rather
    than a truncation — silently transcribing the first seconds of a clip looks like it
    worked (the `impex/parsing.py` stance, applied to audio)."""
    t = await make_tenant("ai-speech-bytes")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=headers)

        # Not a container we recognise — the format comes from the content, never a
        # client-supplied name.
        bad = await c.post(
            "/api/v1/ai/time/transcribe",
            json={"audio": base64.b64encode(b"not audio at all").decode()},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"]["audio"] == "errors.ai_audio_unsupported"

        huge = await c.post(
            "/api/v1/ai/time/transcribe",
            json={"audio": "A" * (MAX_ENCODED_CHARS + 4)},
            headers=headers,
        )
        assert huge.status_code == 413
        assert huge.json()["error"]["message"] == "errors.ai_audio_too_large"


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


def test_wire_tool_names_sanitized_and_mapped_back() -> None:
    """Both providers restrict tool names to ``^[a-zA-Z0-9_-]+$`` — the registry's dotted
    names (companies.find, §12) are translated at the wire and back."""
    import re

    from app.core.ai.providers import ChatMessage, ToolDef, _wire_tool_setup

    tools = [
        ToolDef("companies.find", "d", {"type": "object"}),
        ToolDef("submit_time_entry", "d", {"type": "object"}),
    ]
    history = [
        ChatMessage(role="user", content="q"),
        ChatMessage(
            role="assistant",
            tool_calls=(ToolCall("c1", "companies.find", {"query": "jansen"}),),
        ),
        ChatMessage(role="tool", content="{}", tool_call_id="c1"),
    ]
    wired, force, messages, from_wire = _wire_tool_setup(tools, "companies.find", history)
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]+", t.name) for t in wired)
    assert force == "companies_find"
    assert messages[1].tool_calls[0].name == "companies_find"
    assert from_wire["companies_find"] == "companies.find"
    # An already-valid name passes through untouched.
    assert wired[1].name == "submit_time_entry"


async def test_provider_failure_is_a_502_envelope(client_for, monkeypatch) -> None:
    """A provider refusal is the standard envelope on the non-streaming endpoints, never a
    bare 500 (the settings test button alone shows the provider's own words)."""
    from app.core.ai.providers import AIProviderError

    async def fake(config, **kwargs):  # noqa: ANN001, ANN003
        raise AIProviderError("HTTP 400: bad tool name")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
    t = await make_tenant("ai-provider-err")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post(
            "/api/v1/ai/time/parse", json={"text": "gisteren 2 uur"}, headers=headers
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_provider_error"


async def test_connection_test_passes_when_reasoning_eats_the_budget(
    client_for, monkeypatch
) -> None:
    """#158: a reasoning model can authenticate fine, spend the whole completion budget
    thinking and emit zero visible text. That is a *working* connection — never
    ``ok=False, error=None`` (which the web rendered as "Test mislukt: ?")."""
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat",
        _fake_stream([AIEvent(kind="done", stop_reason="length", tokens_in=9, tokens_out=32)]),
    )
    t = await make_tenant("ai-test-empty")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        result = (await c.post("/api/v1/ai/settings/test", headers=headers)).json()
        assert result["ok"] is True
        assert result["model"]
        assert result["error"] is None


async def test_connection_test_reports_network_failure_readably(
    client_for, monkeypatch
) -> None:
    """#158: httpx errors are not OSError subclasses; without the explicit catch a DNS or
    timeout failure was a raw 500 instead of a test result the settings page can show."""
    import httpx

    async def fake(config, **kwargs):  # noqa: ANN001, ANN003
        raise httpx.ConnectError("dns boom")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("app.core.ai.providers.stream_chat", fake)
    t = await make_tenant("ai-test-net")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        response = await c.post("/api/v1/ai/settings/test", headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is False
        assert "dns boom" in result["error"]
