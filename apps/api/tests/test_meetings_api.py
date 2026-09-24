"""Meetings end to end: recording, folding, the worker run, the filing, the edits — with the
provider faked at the two seams every AI feature here goes through (``stream_chat`` and the
transcription call), so these exercise the platform's own rules with no network."""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pwdlib import PasswordHash
from sqlalchemy import select

from app.config import settings
from app.core.ai.models import AIUsage
from app.core.ai.providers import AIEvent, ToolCall
from app.core.ai.transcribe import Segment, Transcript
from app.core.auth.models import User
from app.core.models import Org
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from app.modules.meetings.jobs import RECORDING_STALE_AFTER_MINUTES, _reap_org, run_pipeline
from app.modules.meetings.models import Meeting
from tests.conftest import add_membership, auth_cookie, make_tenant

_password_hash = PasswordHash.recommended()


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch) -> None:
    """The finish and the retry hand the row to the worker; here the worker is called by hand,
    and the process-wide arq pool would otherwise be bound to the first test's event loop."""

    async def _queued(*args, **kwargs):  # noqa: ANN002, ANN003
        return object()

    monkeypatch.setattr("app.modules.meetings.service.enqueue", _queued)


SETTINGS_BODY = {
    "provider": "anthropic",
    "api_key": "sk-test-super-secret-123",
    "features": {"meeting_assist": {"enabled": True}},
    "speech_provider": "mistral",
    "speech_api_key": "mistral-secret-456",
}
WEBM_HEADER = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
_B64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731

TRANSCRIPT = (
    "Goedemorgen allemaal. We hebben besloten dat de nieuwe homepage vrijdag 3 oktober live gaat. "
    "Sanne pakt de teksten voor de homepage op en levert ze woensdag aan. "
    "Jan van de klant stuurt het nieuwe logo nog deze week."
)


def _fake_transcribe(seconds: int = 900):
    async def fake(config, clip, *, language, diarize=False, timestamps=False):  # noqa: ANN001
        assert clip.extension == "webm"
        return Transcript(
            text=TRANSCRIPT,
            seconds=seconds,
            segments=(
                Segment(0.0, 3.0, "Goedemorgen allemaal.", "speaker_0"),
                Segment(
                    3.0,
                    9.0,
                    "We hebben besloten dat de nieuwe homepage vrijdag 3 oktober live gaat.",
                    "speaker_0",
                ),
                Segment(
                    9.0,
                    15.0,
                    "Sanne pakt de teksten voor de homepage op en levert ze woensdag aan.",
                    "speaker_1",
                ),
                Segment(
                    15.0,
                    20.0,
                    "Jan van de klant stuurt het nieuwe logo nog deze week.",
                    "speaker_2",
                ),
            ),
        )

    return fake


def _fake_stream(events: list[AIEvent]):
    async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
        for event in events:
            yield event

    return fake


def _submit(**fields) -> list[AIEvent]:  # noqa: ANN003
    return [
        AIEvent(
            kind="tool_call", tool_call=ToolCall(id="c1", name="submit_minutes", input=dict(fields))
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=1200, tokens_out=300),
    ]


def _minutes(staff_id: str) -> dict:
    return {
        "summary": "Kick-off van de nieuwe homepage; livegang staat op 3 oktober.",
        "topics": [{"heading": "Homepage", "text": "De teksten en het logo."}],
        "decisions": [
            {
                "text": "Homepage gaat vrijdag 3 oktober live",
                "quote": "de nieuwe homepage vrijdag 3 oktober live gaat",
                "at": 5,
            },
            {
                "text": "Iets dat niemand zei",
                "quote": "we stoppen met de nieuwsbrief per direct",
                "at": 8,
            },
        ],
        "action_items": [
            {
                "title": "Homepageteksten aanleveren",
                "quote": "Sanne pakt de teksten voor de homepage op en levert ze woensdag aan",
                "assignee_user_id": staff_id,
                "due_date": "2026-09-30",
                "at": 10,
            },
            {
                "title": "Nieuw logo sturen",
                "quote": "Jan van de klant stuurt het nieuwe logo nog deze week",
                "owner_label": "Jan (klant)",
                "at": 16,
            },
        ],
        "open_questions": ["Wie regelt de hosting?"],
    }


async def _record(c, headers, *, company_id: str | None = None, chunks: int = 2) -> dict:  # noqa: ANN001
    created = await c.post(
        "/api/v1/meetings",
        json={
            "title": "Kick-off homepage",
            "company_id": company_id,
            "participants_informed": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    meeting = created.json()
    assert meeting["status"] == "recording"
    for seq in range(chunks):
        raw = WEBM_HEADER if seq == 0 else b"\x01" * 300
        res = await c.post(
            f"/api/v1/meetings/{meeting['id']}/chunks",
            json={"seq": seq, "audio": _B64(raw)},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["chunks_received"] == seq + 1
    finished = await c.post(
        f"/api/v1/meetings/{meeting['id']}/finish", json={"duration_seconds": 20}, headers=headers
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "queued"
    return finished.json()


async def _run(org_id: uuid.UUID, meeting_id: str) -> None:
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        await set_current_org(session, org.id)
        await run_pipeline(session, org, uuid.UUID(meeting_id))


# --------------------------------------------------------------------------- #
async def test_a_recording_is_only_opened_when_the_others_were_told(client_for) -> None:
    t = await make_tenant("meet-informed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        res = await c.post("/api/v1/meetings", json={"title": "x"}, headers=headers)
        assert res.status_code == 422, res.text
        assert (
            res.json()["error"]["fields"]["participants_informed"]
            == "meetings.error.participants_informed"
        )


async def test_a_recording_needs_a_provider_that_can_transcribe(client_for) -> None:
    """Off means invisible on the screen; the API says why (409) rather than storing audio
    nobody could ever turn into words."""
    t = await make_tenant("meet-noai")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/meetings", json={"title": "x", "participants_informed": True}, headers=headers
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "ai_feature_disabled"


async def test_the_worker_folds_transcribes_and_drafts(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe(900))
    t = await make_tenant("meet-run")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        await _run(t.org.id, meeting["id"])

        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready", detail
        assert detail["company_name"] == "Nova"
        # The recording exists as one file; the two pieces were folded into it and dropped.
        assert detail["audio_file_id"] and detail["chunks_received"] == 0
        assert detail["transcript_parts"] == 1
        assert [s["speaker"] for s in detail["segments"]] == ["S1", "S1", "S2", "S3"]
        assert detail["duration_seconds"] == 20  # the recorder's count; the provider agreed
        minutes = detail["minutes"]
        assert minutes["summary"].startswith("Kick-off")
        assert [d["verified"] for d in minutes["decisions"]] == [True, False]
        ours, theirs = minutes["action_items"]
        assert ours["assignee_user_id"] == str(t.user.id) and ours["task_id"] is None
        assert theirs["assignee_user_id"] is None and theirs["task_id"] is None
        assert theirs["verified"] is True

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        files = (
            (await session.execute(select(StoredFile).where(StoredFile.org_id == t.org.id)))
            .scalars()
            .all()
        )
        assert len(files) == 1 and files[0].content_id is None
        assert files[0].size_bytes == len(WEBM_HEADER) + 300
        usage = (
            (await session.execute(select(AIUsage).where(AIUsage.org_id == t.org.id)))
            .scalars()
            .all()
        )
        audio = [u for u in usage if u.audio_seconds]
        assert (
            len(audio) == 1
            and audio[0].audio_seconds == 900
            and audio[0].feature == "meeting_assist"
        )
        assert any(u.tokens_in == 1200 for u in usage)


async def test_the_minutes_are_filed_as_a_contact_moment_and_follow_every_edit(
    client_for, tmp_path, monkeypatch
) -> None:
    """No confirm step: the moment the draft lands the worker files it on the client as the
    recorder, and every save of the minutes rewrites that moment — title, words, roster — so
    the timeline never shows words the page no longer does. A moment somebody deleted is filed
    again by the next edit, because the meeting is the record and the moment its mirror."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-filed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready" and detail["interaction_id"], detail

        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert interaction["kind"] == "physical_meeting"
        assert interaction["subject"] == "Kick-off homepage"
        assert interaction["company_id"] == company["id"]
        assert interaction["owner_user_id"] == str(t.user.id)  # the recorder, not the system
        assert "3 oktober" in interaction["body_text"]
        assert "Iets dat niemand zei" in interaction["body_text"]
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "meeting", "entity_id": meeting["id"]},
                headers=headers,
            )
        ).json()
        actions = [row["action"] for row in (trail["items"] if isinstance(trail, dict) else trail)]
        assert "meeting.filed" in actions

        # An edit: the invented decision goes, the title is typed — and the moment follows.
        minutes = detail["minutes"]
        minutes["decisions"] = minutes["decisions"][:1]
        minutes["title"] = "Kick-off homepage (definitief)"
        saved = await c.put(
            f"/api/v1/meetings/{meeting['id']}/minutes", json=minutes, headers=headers
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["title"] == "Kick-off homepage (definitief)"
        assert saved.json()["title_auto"] is False
        assert saved.json()["interaction_id"] == detail["interaction_id"]
        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert interaction["subject"] == "Kick-off homepage (definitief)"
        assert "Iets dat niemand zei" not in interaction["body_text"]
        assert "3 oktober" in interaction["body_text"]

        # Moving the meeting to another client moves the moment with it.
        other = (await c.post("/api/v1/companies", json={"name": "Elders"}, headers=headers)).json()
        moved = await c.patch(
            f"/api/v1/meetings/{meeting['id']}", json={"company_id": other["id"]}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert interaction["company_id"] == other["id"]

        # The moment deleted by hand: the next edit files the meeting again.
        gone = await c.delete(
            f"/api/v1/interactions/{detail['interaction_id']}", headers=headers
        )
        assert gone.status_code in (200, 204), gone.text
        unfiled = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert unfiled["interaction_id"] is None
        saved = await c.put(
            f"/api/v1/meetings/{meeting['id']}/minutes", json=minutes, headers=headers
        )
        assert saved.status_code == 200, saved.text
        refiled = saved.json()["interaction_id"]
        assert refiled and refiled != detail["interaction_id"]
        assert (
            await c.get(f"/api/v1/interactions/{refiled}", headers=headers)
        ).json()["subject"] == "Kick-off homepage (definitief)"


async def test_a_refused_filing_leaves_the_meeting_unfiled_and_the_button_says_why(
    client_for, tmp_path, monkeypatch
) -> None:
    """The interactions module refusing to write must never cost the transcription or an edit:
    the meeting stays unfiled and readable (the refusal is logged), and the page's own button
    (``POST /interaction``) is the strict form that carries the reason — and files it once the
    reason is gone."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-unfiled")
    headers = await auth_cookie(t.user)

    from app.errors import AppError
    from app.modules.interactions.service import InteractionService

    real_create = InteractionService.create

    async def refusing(self, data):  # noqa: ANN001, ANN202
        raise AppError("validation", "errors.interactions_kind_not_manual", status_code=422)

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        monkeypatch.setattr(InteractionService, "create", refusing)
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready" and detail["minutes"], detail
        assert detail["interaction_id"] is None

        # An edit still lands; the moment is still not there.
        saved = await c.put(
            f"/api/v1/meetings/{meeting['id']}/minutes", json=detail["minutes"], headers=headers
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["interaction_id"] is None

        refused = await c.post(f"/api/v1/meetings/{meeting['id']}/interaction", headers=headers)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["message"] == "errors.interactions_kind_not_manual"

        monkeypatch.setattr(InteractionService, "create", real_create)
        filed = await c.post(f"/api/v1/meetings/{meeting['id']}/interaction", headers=headers)
        assert filed.status_code == 200, filed.text
        assert filed.json()["interaction_id"]
        interaction = (
            await c.get(f"/api/v1/interactions/{filed.json()['interaction_id']}", headers=headers)
        ).json()
        assert interaction["subject"] == "Kick-off homepage"


async def test_a_provider_failure_ends_on_failed_with_a_reason(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    async def refuse(config, clip, **kwargs):  # noqa: ANN001, ANN003
        from app.core.ai.providers import AIProviderError

        raise AIProviderError("401 invalid key")

    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", refuse)
    t = await make_tenant("meet-fail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        meeting = await _record(c, headers)
        await _run(t.org.id, meeting["id"])
        status = (await c.get(f"/api/v1/meetings/{meeting['id']}/status", headers=headers)).json()
        assert status["status"] == "failed" and status["error_key"] == "errors.ai_provider_error"
        # The audio survived the failure: a retry reads the folded recording back.
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["audio_file_id"] is not None
        monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        retried = await c.post(f"/api/v1/meetings/{meeting['id']}/retry", headers=headers)
        assert retried.status_code == 200 and retried.json()["status"] == "queued"
        await _run(t.org.id, meeting["id"])
        assert (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()[
            "status"
        ] == "ready"


async def test_meetings_tenant_isolation(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    a = await make_tenant("meet-iso-a")
    b = await make_tenant("meet-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        await ca.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=a_headers)
        meeting = await _record(ca, a_headers)
    async with client_for(b.host) as cb:
        await cb.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=b_headers)
        assert (
            await cb.get(f"/api/v1/meetings/{meeting['id']}", headers=b_headers)
        ).status_code == 404
        assert (await cb.get("/api/v1/meetings", headers=b_headers)).json()["total"] == 0
        assert (
            await cb.post(
                f"/api/v1/meetings/{meeting['id']}/chunks",
                json={"seq": 5, "audio": _B64(b"\x01" * 10)},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/meetings/{meeting['id']}", headers=b_headers)
        ).status_code == 404


async def test_deleting_takes_the_audio_and_needs_its_own_key(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("meet-delete")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        member = User(
            id=uuid.uuid4(),
            email="member-meet@example.com",
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(member)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.id, role="member")
        await session.commit()
    member_headers = await auth_cookie(member, t.org.id)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        meeting = await _record(c, member_headers)  # a member records
        # …and may not delete: the seeded member role holds read and write, not delete.
        assert (
            await c.delete(f"/api/v1/meetings/{meeting['id']}", headers=member_headers)
        ).status_code == 403
        assert (
            await c.delete(f"/api/v1/meetings/{meeting['id']}", headers=headers)
        ).status_code == 204
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.scalar(select(Meeting).where(Meeting.org_id == t.org.id))) is None
        left = (
            (await session.execute(select(StoredFile).where(StoredFile.org_id == t.org.id)))
            .scalars()
            .all()
        )
        assert left == []


async def test_the_list_is_two_statements_however_many_rows(
    client_for, tmp_path, monkeypatch, count_queries
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("meet-perf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        for _ in range(6):
            await _record(c, headers, company_id=company["id"], chunks=1)
        with count_queries() as counter:
            res = await c.get("/api/v1/meetings?limit=50", headers=headers)
        assert res.status_code == 200 and res.json()["total"] == 6
        assert len(res.json()["items"]) == 6 and res.json()["items"][0]["company_name"] == "Nova"
        assert len(counter.matching("from meetings")) == 2  # the page and its count
        # The labels, batched: one ``IN`` over every client on the page (the trash anchor's
        # ``NOT EXISTS`` inside the two statements above also names ``companies``, by id).
        assert len(counter.matching("companies.id in")) == 1


def _now() -> datetime:
    return datetime.now(UTC)


# --- the roster --------------------------------------------------------------------- #
def _minutes_with_contact(staff_id: str, contact_id: str) -> dict:
    minutes = _minutes(staff_id)
    minutes["action_items"][1] = {
        "title": "Nieuw logo sturen",
        "quote": "Jan van de klant stuurt het nieuwe logo nog deze week",
        "owner_contact_id": contact_id,
        "at": 16,
    }
    return minutes


async def _contact(c, headers, company_id: str, first: str, last: str) -> str:  # noqa: ANN001
    res = await c.post(
        "/api/v1/contacts",
        json={"first_name": first, "last_name": last, "company_ids": [company_id]},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_participants_are_people_and_a_contacts_promise_becomes_their_task(
    client_for, tmp_path, monkeypatch
) -> None:
    """The roster names a colleague, a contact and a stranger; the labels are paired in review;
    the model is shown the PARTICIPANTS block and grounds ``owner_contact_id`` in it; the
    contact is on the contact moment's roster, and making a task of their promise assigns it
    to *them* — and the minutes print by side, then by person."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-roster")
    headers = await auth_cookie(t.user)
    seen_prompts: list[str] = []

    def _capturing(events):  # noqa: ANN001, ANN202
        async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
            seen_prompts.append(kwargs.get("system") or "")
            for event in events:
                yield event

        return fake

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        jan = await _contact(c, headers, company["id"], "Jan", "de Vries")
        created = await c.post(
            "/api/v1/meetings",
            json={
                "title": "Kick-off homepage",
                "company_id": company["id"],
                "participants_informed": True,
                "participants": [
                    {"name": "Jan de Vries", "contact_id": jan},
                    {"name": "Piet (drukker)"},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        roster = created.json()["participants"]
        # The recorder is in the room by definition, first.
        assert roster[0]["user_id"] == str(t.user.id) and roster[0]["name"]
        assert [p["name"] for p in roster[1:]] == ["Jan de Vries", "Piet (drukker)"]
        meeting_id = created.json()["id"]
        for seq in range(2):
            raw = WEBM_HEADER if seq == 0 else b"\x01" * 300
            await c.post(
                f"/api/v1/meetings/{meeting_id}/chunks",
                json={"seq": seq, "audio": _B64(raw)},
                headers=headers,
            )
        await c.post(
            f"/api/v1/meetings/{meeting_id}/finish", json={"duration_seconds": 20}, headers=headers
        )
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _capturing(_submit(**_minutes_with_contact(str(t.user.id), jan))),
        )
        await _run(t.org.id, meeting_id)
        detail = (await c.get(f"/api/v1/meetings/{meeting_id}", headers=headers)).json()
        assert detail["status"] == "ready" and detail["diarized"] is True
        assert f"Jan de Vries\tcontact\t{jan}" in seen_prompts[-1]
        theirs = detail["minutes"]["action_items"][1]
        assert theirs["owner_contact_id"] == jan

        # Pair the labels with the people. A label names one person: S3 twice is refused.
        roster = detail["participants"]
        roster[0]["speaker"], roster[1]["speaker"], roster[2]["speaker"] = "S1", "S3", "S3"
        res = await c.put(
            f"/api/v1/meetings/{meeting_id}/participants",
            json={"participants": roster},
            headers=headers,
        )
        assert res.status_code == 422
        assert res.json()["error"]["fields"]["participants"] == "meetings.error.speaker_twice"
        roster[2]["speaker"] = "S2"
        res = await c.put(
            f"/api/v1/meetings/{meeting_id}/participants",
            json={"participants": roster},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["speakers"] == {
            "S1": roster[0]["name"],
            "S3": "Jan de Vries",
            "S2": "Piet (drukker)",
        }

        # A contact this caller may not see is refused through the directory seam.
        other = await make_tenant("meet-roster-b")
        other_headers = await auth_cookie(other.user)
        async with client_for(other.host) as cb:
            await cb.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=other_headers)
            company_b = (
                await cb.post("/api/v1/companies", json={"name": "Elders"}, headers=other_headers)
            ).json()
            stranger = await _contact(cb, other_headers, company_b["id"], "Karel", "Vreemd")
        res = await c.put(
            f"/api/v1/meetings/{meeting_id}/participants",
            json={"participants": [*roster, {"name": "Karel", "contact_id": stranger}]},
            headers=headers,
        )
        assert res.status_code == 422, res.text

        # The roster was saved after the filing: the moment's roster follows it.
        detail = (await c.get(f"/api/v1/meetings/{meeting_id}", headers=headers)).json()
        assert detail["interaction_id"], detail
        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert [x["id"] for x in interaction["contacts"]] == [jan]
        body = interaction["body_text"]
        assert body.index("## Aanwezig") < body.index("Jan de Vries, Piet (drukker)")
        assert body.index("### Voor ons") < body.index("- Homepageteksten aanleveren")
        assert (
            body.index("### Voor de klant")
            < body.index("**Jan de Vries**")
            < body.index("- Nieuw logo sturen")
        )
        # The client's promise, chased: a task assigned to the contact, listed on the moment.
        made = await c.post(
            f"/api/v1/meetings/{meeting_id}/action-items/task",
            json={
                "index": 1,
                "title": "Nieuw logo sturen",
                "due_date": "2026-09-30",
                "assignee_contact_id": jan,
            },
            headers=headers,
        )
        assert made.status_code == 201, made.text
        logo = (await c.get(f"/api/v1/tasks/{made.json()['task_id']}", headers=headers)).json()
        assert logo["title"] == "Nieuw logo sturen"
        assert logo["assignee_contact_id"] == jan and logo["assignee_user_id"] is None
        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert [x["id"] for x in interaction["tasks"]] == [made.json()["task_id"]]


async def test_redraft_writes_the_minutes_again_without_transcribing(
    client_for, tmp_path, monkeypatch
) -> None:
    """After the speakers are named the draft is written again over the stored transcript: the
    provider's transcription is *not* called, the new prompt carries the labels, the row ends on
    review again. A row with a legacy ``speakers`` map still reads as a roster."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-redraft")
    headers = await auth_cookie(t.user)
    prompts: list[str] = []

    def _capturing(events):  # noqa: ANN001, ANN202
        async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
            prompts.append(kwargs.get("system") or "")
            for event in events:
                yield event

        return fake

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _capturing(_submit(**_minutes(str(t.user.id))))
        )
        await _run(t.org.id, meeting["id"])
        assert "S2\t" not in prompts[-1]

        # The legacy shape: a row written before the roster existed.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row = await session.get(Meeting, uuid.UUID(meeting["id"]))
            row.participants = None
            row.speakers = {"S2": "Sanne"}
            await session.commit()
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["participants"] == [
            {"name": "Sanne", "user_id": None, "contact_id": None, "speaker": "S2"}
        ]
        assert detail["speakers"] == {"S2": "Sanne"}

        async def never(config, clip, **kwargs):  # noqa: ANN001, ANN003
            raise AssertionError("a redraft must not transcribe again")

        monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", never)
        res = await c.post(f"/api/v1/meetings/{meeting['id']}/redraft", headers=headers)
        assert res.status_code == 200 and res.json()["status"] == "queued"
        async with async_session_maker() as session:
            org = await session.get(Org, t.org.id)
            await set_current_org(session, org.id)
            await run_pipeline(session, org, uuid.UUID(meeting["id"]), stage="minutes")
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready", detail
        assert "S2\tSanne\tother\t-" in prompts[-1]
        # The audio was metered once: the redraft spent none.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        usage = (
            (await session.execute(select(AIUsage).where(AIUsage.org_id == t.org.id)))
            .scalars()
            .all()
        )
        assert len([u for u in usage if u.audio_seconds]) == 1


async def test_the_capability_says_whether_the_speech_model_labels_speakers(client_for) -> None:
    """``speech_diarize`` rides ``/meta/me`` beside ``speech``: Voxtral labels, a text-only
    OpenAI model does not — and the recorder reads it before a minute is recorded."""
    from app.core.ai.service import invalidate_features_cache

    t = await make_tenant("meet-diarize-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        invalidate_features_cache(t.org.id)
        features = (await c.get("/api/v1/meta/me", headers=headers)).json()["ai_features"]
        assert "speech" in features and "speech_diarize" in features

        await c.put(
            "/api/v1/ai/settings",
            json={
                **SETTINGS_BODY,
                "speech_provider": "openai",
                "speech_api_key": "sk-speech",
                "speech_model": "gpt-transcribe",
            },
            headers=headers,
        )
        invalidate_features_cache(t.org.id)
        features = (await c.get("/api/v1/meta/me", headers=headers)).json()["ai_features"]
        assert "speech" in features and "speech_diarize" not in features

        await c.put(
            "/api/v1/ai/settings",
            json={
                **SETTINGS_BODY,
                "speech_provider": "openai",
                "speech_api_key": "sk-speech",
                "speech_model": "gpt-4o-transcribe-diarize",
            },
            headers=headers,
        )
        invalidate_features_cache(t.org.id)
        assert (
            "speech_diarize"
            in (await c.get("/api/v1/meta/me", headers=headers)).json()["ai_features"]
        )


# --------------------------------------------------------------------------- #
# Who may see a meeting (§15): the key, the horizon, and the portal — for the record and for
# its recording alike.
# --------------------------------------------------------------------------- #
async def _staff(t, email: str, *, role: str = "member") -> tuple[User, uuid.UUID]:  # noqa: ANN001
    """A second login in ``t``'s org, holding the seeded ``role``; returns it with its
    membership id, which is what a company group and a role set are keyed on."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, user.id, role=role)
        membership_id = membership.id
        await session.commit()
    return user, membership_id


async def _scope_to(c, owner_h, *, company_id: str, membership_id: uuid.UUID, name: str) -> None:  # noqa: ANN001
    """Restrict one membership to a company group holding exactly ``company_id``."""
    group = (await c.post("/api/v1/companies/groups", json={"name": name}, headers=owner_h)).json()
    assert (
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [company_id]},
            headers=owner_h,
        )
    ).status_code == 204
    assert (
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [str(membership_id)]},
            headers=owner_h,
        )
    ).status_code == 204


async def _folded_recording(c, owner_h, t, monkeypatch, *, company_id: str) -> tuple[dict, str]:  # noqa: ANN001
    """A meeting on ``company_id`` run through the worker, and the id of its folded audio."""
    meeting = await _record(c, owner_h, company_id=company_id)
    monkeypatch.setattr(
        "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
    )
    await _run(t.org.id, meeting["id"])
    detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=owner_h)).json()
    assert detail["status"] == "ready" and detail["audio_file_id"], detail
    return meeting, detail["audio_file_id"]


async def test_the_recording_reads_exactly_when_the_meeting_does(
    client_for, tmp_path, monkeypatch
) -> None:
    """``meeting`` is a record-gated file host: the bytes, the thumbnail route and the file
    list answer the meeting's own read key and then its horizon — never only the tenant.

    Before this the audio was a ``files`` row on a host nobody had gated, so any signed-in
    member holding the id could pull the recording of a meeting they could not open.
    """
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe(900))
    t = await make_tenant("meet-gate")
    owner_h = await auth_cookie(t.user)
    reader, _ = await _staff(t, "reader-gate@example.com")
    scoped, scoped_mid = await _staff(t, "scoped-gate@example.com")
    outsider, outsider_mid = await _staff(t, "outsider-gate@example.com")
    reader_h = await auth_cookie(reader, t.org.id)
    scoped_h = await auth_cookie(scoped, t.org.id)
    outsider_h = await auth_cookie(outsider, t.org.id)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=owner_h)
        alpha = (await c.post("/api/v1/companies", json={"name": "Alpha"}, headers=owner_h)).json()
        beta = (await c.post("/api/v1/companies", json={"name": "Beta"}, headers=owner_h)).json()
        # ``scoped`` is a member restricted to Beta; the meeting will be Alpha's.
        await _scope_to(c, owner_h, company_id=beta["id"], membership_id=scoped_mid, name="Beta")
        # ``outsider`` is unrestricted and holds a role with no meetings key at all.
        role = await c.post(
            "/api/v1/roles",
            json={
                "key": "archivaris",
                "name_i18n": {"en": "Archivist"},
                "permissions": ["companies.company.read"],
            },
            headers=owner_h,
        )
        assert role.status_code in (200, 201), role.text
        assert (
            await c.put(
                f"/api/v1/members/{outsider_mid}/roles",
                json={"role_ids": [role.json()["id"]]},
                headers=owner_h,
            )
        ).status_code == 200

        meeting, audio_id = await _folded_recording(
            c, owner_h, t, monkeypatch, company_id=alpha["id"]
        )
        record = f"/api/v1/meetings/{meeting['id']}"
        audio = f"/api/v1/files/{audio_id}"
        listing = f"/api/v1/files?entity_type=meeting&entity_id={meeting['id']}"

        # Control: a colleague who may open the meeting gets the recording and its listing.
        assert (await c.get(record, headers=reader_h)).status_code == 200
        assert (await c.get(audio, headers=reader_h)).status_code == 200
        assert [f["id"] for f in (await c.get(listing, headers=reader_h)).json()] == [audio_id]

        # Outside the horizon: the meeting is a 404, and so are its bytes and its listing.
        assert (await c.get(record, headers=scoped_h)).status_code == 404
        assert (await c.get(audio, headers=scoped_h)).status_code == 404
        assert (await c.get(f"{audio}/thumbnail", headers=scoped_h)).status_code == 404
        assert (await c.get(listing, headers=scoped_h)).json() == []

        # Without the key: the route refuses, and the bytes answer the record's own 404 — a
        # tenant-scoped row is not a readable one.
        assert (await c.get(record, headers=outsider_h)).status_code == 403
        assert (await c.get(audio, headers=outsider_h)).status_code == 404
        assert (await c.get(f"{audio}/thumbnail", headers=outsider_h)).status_code == 404
        assert (await c.get(listing, headers=outsider_h)).json() == []


async def test_a_client_never_reads_a_meeting_even_holding_the_key(
    client_for, tmp_path, monkeypatch
) -> None:
    """``Meeting.__portal_horizon_clause__`` is nothing: a client scoped to the meeting's own
    company, whose tenant has granted the ``client`` role the read key, still gets an empty
    list, a 404 on every id, and no recording. The confirmed contact moment is what a client
    is owed, and ``interactions`` serves it under its own rules (docs/MEETINGS.md)."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe(900))
    t = await make_tenant("meet-portal")
    owner_h = await auth_cookie(t.user)
    guest, guest_mid = await _staff(t, "client-portal@example.com", role="client")
    guest_h = await auth_cookie(guest, t.org.id)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=owner_h)
        alpha = (await c.post("/api/v1/companies", json={"name": "Alpha"}, headers=owner_h)).json()
        await _scope_to(c, owner_h, company_id=alpha["id"], membership_id=guest_mid, name="Alpha")
        roles = (await c.get("/api/v1/roles", headers=owner_h)).json()
        client_role = next(r for r in roles if r["key"] == "client")
        granted = await c.patch(
            f"/api/v1/roles/{client_role['id']}",
            json={"permissions": [*client_role["permissions"], "meetings.meeting.read"]},
            headers=owner_h,
        )
        assert granted.status_code == 200, granted.text

        meeting, audio_id = await _folded_recording(
            c, owner_h, t, monkeypatch, company_id=alpha["id"]
        )

        listed = await c.get("/api/v1/meetings", headers=guest_h)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == [] and listed.json()["total"] == 0
        assert (
            await c.get("/api/v1/meetings", params={"company_id": alpha["id"]}, headers=guest_h)
        ).json()["total"] == 0
        assert (
            await c.get(f"/api/v1/meetings/{meeting['id']}", headers=guest_h)
        ).status_code == 404
        assert (
            await c.get(f"/api/v1/meetings/{meeting['id']}/status", headers=guest_h)
        ).status_code == 404
        assert (await c.get(f"/api/v1/files/{audio_id}", headers=guest_h)).status_code == 404
        assert (
            await c.get(
                f"/api/v1/files?entity_type=meeting&entity_id={meeting['id']}", headers=guest_h
            )
        ).json() == []
        # Recording was already refused outright, and stays so.
        assert (
            await c.post(
                "/api/v1/meetings",
                json={"title": "x", "company_id": alpha["id"], "participants_informed": True},
                headers=guest_h,
            )
        ).status_code == 403


async def test_a_run_the_worker_restart_cut_short_is_resumed(
    client_for, tmp_path, monkeypatch
) -> None:
    """The worker rolls stop-first on a redeploy: arq cancels the running job and queues it
    again, and the second run finds the row still stamped with the state the first one was in.
    Standing down there left the meeting to the reaper (ninety minutes, then ``failed``, then a
    person pressing retry) for a restart that took seconds — so a row in a worker state is
    resumed, and from where the words are: a transcript already stored is not bought twice."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    transcriptions: list[int] = []
    inner = _fake_transcribe(900)

    async def counting(config, clip, **kwargs):  # noqa: ANN001, ANN003
        transcriptions.append(1)
        return await inner(config, clip, **kwargs)

    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", counting)
    t = await make_tenant("meet-resume")
    headers = await auth_cookie(t.user)

    async def left_on(status: str, meeting_id: str) -> None:
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row = await session.get(Meeting, uuid.UUID(meeting_id))
            row.status = status
            row.status_at = datetime.now(UTC)
            row.minutes = None
            await session.commit()

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        meeting = await _record(c, headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        # Stopped while transcribing: nothing was written, so the words are read once.
        await left_on("transcribing", meeting["id"])
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready", detail
        assert len(transcriptions) == 1
        # Stopped while summarising: the transcript is on the row, so the resume starts at the
        # minutes and the provider is not asked for the words again.
        await left_on("summarising", meeting["id"])
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "ready" and detail["minutes"], detail
        assert len(transcriptions) == 1
        # A row a person is reviewing is not a run to resume: the worker still stands down.
        await _run(t.org.id, meeting["id"])
        assert (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()[
            "status"
        ] == "ready"


# --------------------------------------------------------------------------- #
# The recorder that never sent a stop.
# --------------------------------------------------------------------------- #


async def _age_recording(org_id: uuid.UUID, meeting_id: str, minutes: int) -> None:
    """Pretend the last piece landed `minutes` ago — what a dead tab looks like."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.get(Meeting, uuid.UUID(meeting_id))
        row.status_at = datetime.now(UTC) - timedelta(minutes=minutes)
        await session.commit()


async def _reap(org_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        await set_current_org(session, org.id)
        await _reap_org(org, session)
        await session.commit()


async def test_a_recording_nobody_is_feeding_is_ended_by_the_server(
    client_for, tmp_path, monkeypatch
) -> None:
    """The stop is the one message a dead tab cannot send, so the server sends it.

    A recording that stored pieces is *queued* — the same act as the recorder's own finish —
    because the meeting is on the server and only the stop is missing. Before this, a
    ``recording`` row was the one state nothing ever ended: three hours after a phone locked
    mid-meeting, the screen still said a recording was running and the only control on it was
    Delete.
    """
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    queued: list[tuple] = []

    async def _capture(*args, **kwargs):  # noqa: ANN002, ANN003
        queued.append(args)
        return object()

    monkeypatch.setattr("app.modules.meetings.jobs.enqueue", _capture)
    t = await make_tenant("meet-reap-pieces")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post(
            "/api/v1/meetings",
            json={"title": "Kwartaaloverleg", "participants_informed": True},
            headers=headers,
        )
        meeting = created.json()
        for seq in range(2):
            raw = WEBM_HEADER if seq == 0 else b"\x01" * 300
            await c.post(
                f"/api/v1/meetings/{meeting['id']}/chunks",
                json={"seq": seq, "audio": _B64(raw)},
                headers=headers,
            )

        # Still being recorded: a reap now must leave it alone, or it would 409 every
        # remaining piece and lose the rest of a live meeting to save the start of it.
        await _reap(t.org.id)
        assert (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()[
            "status"
        ] == "recording"
        assert queued == []

        await _age_recording(t.org.id, meeting["id"], RECORDING_STALE_AFTER_MINUTES + 1)
        await _reap(t.org.id)
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "queued", detail
        assert detail["error_key"] is None
        assert queued and queued[0][0] == "meetings_process" and queued[0][2] == meeting["id"]


async def test_a_recording_that_never_arrived_is_failed_and_says_so(client_for) -> None:
    """Zero pieces is not a recording, it is a row claiming to be one.

    This is the shape that stranded a three-hour meeting: the row was created before the
    microphone was acquired, the capture never began, and nothing on the server disagreed with
    a screen that went on saying "de opname loopt". It is failed with the reason rather than
    deleted, because the title, the client and the roster the person typed did reach us and are
    the only half worth keeping.
    """
    t = await make_tenant("meet-reap-empty")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post(
            "/api/v1/meetings",
            json={"title": "Kick-off", "participants_informed": True},
            headers=headers,
        )
        meeting = created.json()
        await _age_recording(t.org.id, meeting["id"], RECORDING_STALE_AFTER_MINUTES + 1)
        await _reap(t.org.id)

        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["status"] == "failed", detail
        assert detail["error_key"] == "meetings.error.abandoned"
        assert detail["title"] == "Kick-off"

        # And the colleague who pressed record is told, rather than finding out days later by
        # opening the row: silence is what made the incident behind this expensive.
        inbox = (await c.get("/api/v1/notifications", headers=headers)).json()
        lost = [i for i in inbox["items"] if i["event_type"] == "meeting.lost"]
        assert len(lost) == 1, inbox
        assert lost[0]["payload"]["title"] == "Kick-off"


async def test_a_piece_keeps_the_recording_alive(client_for, tmp_path, monkeypatch) -> None:
    """``status_at`` is stamped by every piece, so a live recorder can never look silent — and
    only a piece may say so: a title edited mid-recording bumps ``updated_at`` and must not buy
    a dead tab another twenty minutes."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("meet-reap-alive")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        created = await c.post(
            "/api/v1/meetings",
            json={"title": "Standup", "participants_informed": True},
            headers=headers,
        )
        meeting = created.json()
        await _age_recording(t.org.id, meeting["id"], RECORDING_STALE_AFTER_MINUTES + 1)

        # A title edit is not the recording saying anything.
        await c.patch(
            f"/api/v1/meetings/{meeting['id']}", json={"title": "Standup dinsdag"}, headers=headers
        )
        await _reap(t.org.id)
        assert (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()[
            "status"
        ] == "failed"

        # A piece is.
        second = await c.post(
            "/api/v1/meetings",
            json={"title": "Standup 2", "participants_informed": True},
            headers=headers,
        )
        second_id = second.json()["id"]
        await _age_recording(t.org.id, second_id, RECORDING_STALE_AFTER_MINUTES + 1)
        await c.post(
            f"/api/v1/meetings/{second_id}/chunks",
            json={"seq": 0, "audio": _B64(WEBM_HEADER)},
            headers=headers,
        )
        await _reap(t.org.id)
        assert (await c.get(f"/api/v1/meetings/{second_id}", headers=headers)).json()[
            "status"
        ] == "recording"


# --------------------------------------------------------------------------- #
# A meeting names itself, books its hours, and makes a task with schakl's draft.
# --------------------------------------------------------------------------- #
def _submit_task(**fields) -> list[AIEvent]:  # noqa: ANN003
    return [
        AIEvent(
            kind="tool_call", tool_call=ToolCall(id="c2", name="submit_task", input=dict(fields))
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=300, tokens_out=90),
    ]


async def _record_minutes(c, headers, meeting_id: str, *, duration: int = 20) -> None:  # noqa: ANN001
    for seq, raw in enumerate((WEBM_HEADER, b"\x01" * 300)):
        await c.post(
            f"/api/v1/meetings/{meeting_id}/chunks",
            json={"seq": seq, "audio": _B64(raw)},
            headers=headers,
        )
    finished = await c.post(
        f"/api/v1/meetings/{meeting_id}/finish",
        json={"duration_seconds": duration},
        headers=headers,
    )
    assert finished.status_code == 200, finished.text


async def test_a_meeting_left_unnamed_is_named_by_the_client_and_then_by_the_minutes(
    client_for, tmp_path, monkeypatch
) -> None:
    """No title typed: the row is named after the client and the day, marked as schakl's, and
    renamed once the minutes say what it was about. A title a person typed is never touched."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-autotitle")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        created = await c.post(
            "/api/v1/meetings",
            json={"company_id": company["id"], "participants_informed": True},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        meeting = created.json()
        assert meeting["title_auto"] is True
        assert meeting["title"].startswith("Bespreking met Nova · ")
        await _record_minutes(c, headers, meeting["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(_submit(title="Kick-off homepage", **_minutes(str(t.user.id)))),
        )
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["title"] == "Kick-off homepage" and detail["title_auto"] is True
        assert detail["minutes"]["time_note"] is None

        # A typed title is the person's: the same draft does not rename it.
        typed = await c.post(
            "/api/v1/meetings",
            json={"title": "Mijn eigen titel", "participants_informed": True},
            headers=headers,
        )
        assert typed.json()["title_auto"] is False
        edited = await c.patch(
            f"/api/v1/meetings/{meeting['id']}", json={"title": "Zelf gekozen"}, headers=headers
        )
        assert edited.json()["title_auto"] is False


async def test_the_hours_are_booked_for_the_colleagues_named(
    client_for, tmp_path, monkeypatch
) -> None:
    """``POST /time`` writes one entry per colleague ticked, for the meeting's length, typed
    after the contact moment's kind and filed on it; the page reads them back by name. A
    member may book their own hours and not a colleague's (``time.entry.write:any``), and a
    refusal writes nothing."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-hours")
    headers = await auth_cookie(t.user)
    colleague, _mid = await _staff(t, "collega-meet@example.com")
    colleague_headers = await auth_cookie(colleague, t.org.id)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        created = await c.post(
            "/api/v1/meetings",
            json={
                "title": "Kick-off homepage",
                "company_id": company["id"],
                "participants_informed": True,
                "participants": [{"name": "Collega", "user_id": str(colleague.id)}],
            },
            headers=headers,
        )
        meeting = created.json()
        await _record_minutes(c, headers, meeting["id"], duration=45 * 60)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _submit(time_note="Kick-off homepage met Nova", **_minutes(str(t.user.id)))
            ),
        )
        await _run(t.org.id, meeting["id"])
        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert detail["minutes"]["time_note"] == "Kick-off homepage met Nova"
        assert detail["can_log_time_own"] and detail["can_log_time_any"]
        member_view = (
            await c.get(f"/api/v1/meetings/{meeting['id']}", headers=colleague_headers)
        ).json()
        assert member_view["can_log_time_own"] and not member_view["can_log_time_any"]

        # A member booking the owner's hours is refused before anything is written.
        refused = await c.post(
            f"/api/v1/meetings/{meeting['id']}/time",
            json={"user_ids": [str(t.user.id), str(colleague.id)]},
            headers=colleague_headers,
        )
        assert refused.status_code == 403, refused.text
        assert (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()[
            "time_entries"
        ] == []

        res = await c.post(
            f"/api/v1/meetings/{meeting['id']}/time",
            json={"user_ids": [str(t.user.id), str(colleague.id)]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        result = res.json()
        assert sorted(e["minutes"] for e in result["time_entries"]) == [45, 45]
        assert {e["user_id"] for e in result["time_entries"]} == {
            str(t.user.id),
            str(colleague.id),
        }
        assert {e["user_name"] for e in result["time_entries"]} >= {"Collega"}
        assert result["interaction_id"], result

        done = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        assert len(done["time_entries"]) == 2
        entries = (
            await c.get(
                "/api/v1/time/entries",
                params={"user_id": str(colleague.id)},
                headers=headers,
            )
        ).json()
        rows = entries["items"] if isinstance(entries, dict) else entries
        booked = [r for r in rows if r["interaction_id"] == result["interaction_id"]]
        assert len(booked) == 1
        assert booked[0]["minutes"] == 45
        assert booked[0]["description"] == "Kick-off homepage met Nova"
        assert booked[0]["company_id"] == company["id"]
        assert booked[0]["entry_type_key"] == "physical_meeting"


async def test_an_action_item_becomes_a_task_with_schakls_draft(
    client_for, tmp_path, monkeypatch
) -> None:
    """The page asks schakl to fill in the task for one action item — steps, a spoken
    deadline, the owner — grounded in the transcript around it and pinned to the meeting's
    client; the reviewed draft is created in one call, the item remembers its task (and keeps
    it through every later save of the minutes), and the contact moment lists it at once."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant("meet-itemtask")
    headers = await auth_cookie(t.user)
    seen_docs: list[str] = []

    def _capturing(events):  # noqa: ANN001, ANN202
        async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
            seen_docs.append(kwargs["messages"][0].content)
            for event in events:
                yield event

        return fake

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        await _run(t.org.id, meeting["id"])

        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _capturing(
                _submit_task(
                    title="Homepageteksten aanleveren",
                    description="Teksten voor de drie hoofdpagina's.",
                    due_date="2026-09-30",
                    checklist_items=[{"title": "Home"}, {"title": "Over ons"}],
                    company_id=str(uuid.uuid4()),  # never shown: dropped, the meeting's wins
                    assignee_user_id=str(t.user.id),
                )
            ),
        )
        drafted = await c.post(
            f"/api/v1/meetings/{meeting['id']}/action-items/draft-task",
            json={"index": 0},
            headers=headers,
        )
        assert drafted.status_code == 200, drafted.text
        draft = drafted.json()
        assert draft["company_id"] == company["id"]
        assert draft["due_date"] == "2026-09-30"
        assert [s["title"] for s in draft["checklist_items"]] == ["Home", "Over ons"]
        assert draft["assignee_user_id"] == str(t.user.id)
        # The model read the item and the words around it, as data.
        assert "Sanne pakt de teksten" in seen_docs[-1]
        assert "transcript_around_it" in seen_docs[-1]
        assert (
            await c.post(
                f"/api/v1/meetings/{meeting['id']}/action-items/draft-task",
                json={"index": 7},
                headers=headers,
            )
        ).status_code == 404

        made = await c.post(
            f"/api/v1/meetings/{meeting['id']}/action-items/task",
            json={
                "index": 0,
                "title": draft["title"],
                "description": draft["description"],
                "due_date": draft["due_date"],
                "assignee_user_id": draft["assignee_user_id"],
                "checklist_items": draft["checklist_items"],
            },
            headers=headers,
        )
        assert made.status_code == 201, made.text
        task_id = made.json()["task_id"]
        item = made.json()["meeting"]["minutes"]["action_items"][0]
        assert item["task_id"] == task_id
        task = (await c.get(f"/api/v1/tasks/{task_id}", headers=headers)).json()
        assert task["company_id"] == company["id"]
        assert task["due_date"] == "2026-09-30"
        assert "Teksten voor de drie hoofdpagina's." in task["description"]
        assert "Sanne pakt de teksten" in task["description"]
        assert [i["title"] for i in task["checklists"][0]["items"]] == ["Home", "Over ons"]
        # Once is enough.
        again = await c.post(
            f"/api/v1/meetings/{meeting['id']}/action-items/task",
            json={"index": 0, "title": "x", "due_date": "2026-09-30"},
            headers=headers,
        )
        assert again.status_code == 409

        detail = (await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)).json()
        interaction = (
            await c.get(f"/api/v1/interactions/{detail['interaction_id']}", headers=headers)
        ).json()
        assert [x["id"] for x in interaction["tasks"]] == [task_id]
        tasks = (
            await c.get("/api/v1/tasks", params={"company_id": company["id"]}, headers=headers)
        ).json()
        assert len(tasks["items"]) == 1

        # A save of the minutes that does not carry the link keeps it (matched on the words,
        # so a reordered list cannot hand one item another item's task); a save that carries
        # it may rename the item freely.
        minutes = detail["minutes"]
        minutes["action_items"][0]["task_id"] = None
        minutes["action_items"].reverse()
        saved = await c.put(
            f"/api/v1/meetings/{meeting['id']}/minutes", json=minutes, headers=headers
        )
        assert saved.status_code == 200, saved.text
        items = saved.json()["minutes"]["action_items"]
        assert [i["task_id"] for i in items] == [None, task_id]
        items[1]["title"] = "Homepageteksten aanleveren (herzien)"
        saved = await c.put(
            f"/api/v1/meetings/{meeting['id']}/minutes",
            json={**saved.json()["minutes"], "action_items": items},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["minutes"]["action_items"][1]["task_id"] == task_id
