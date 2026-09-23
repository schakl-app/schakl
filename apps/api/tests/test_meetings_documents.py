"""Meetings, the second half: the org's settings, the notification, the transcript as a file,
the minutes document, the AI box and the curated tools — with the provider faked at the same
two seams ``test_meetings_api`` uses, so nothing here touches the network."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.ai.providers import AIEvent, ToolCall
from app.core.ai.tools import available_tools, run_tool
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.meetings.assist import revision_from_call
from app.modules.meetings.mcp import MEETING_MCP_TOOLS
from app.modules.meetings.models import Meeting
from app.modules.meetings.schemas import MeetingDetail, MeetingTranscript, TranscriptSegment
from app.modules.meetings.transcript import render_transcript
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.notifications.prefs import effective_email_matrix
from tests.conftest import auth_cookie, make_tenant
from tests.test_files_api import _PNG
from tests.test_meetings_api import (
    SETTINGS_BODY,
    _fake_stream,
    _fake_transcribe,
    _minutes,
    _no_queue,  # noqa: F401 — the autouse fixture: the queue is faked here too
    _record,
    _run,
    _submit,
)
from tests.test_notifications_fanout import _member


async def _minuted(client_for, tmp_path, monkeypatch, slug: str):  # noqa: ANN001, ANN202
    """A tenant with one meeting through the whole pipeline, on ``review``."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr("app.modules.meetings.pipeline.provider_transcribe", _fake_transcribe())
    t = await make_tenant(slug)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = (await c.post("/api/v1/companies", json={"name": "Nova"}, headers=headers)).json()
        meeting = await _record(c, headers, company_id=company["id"])
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(**_minutes(str(t.user.id))))
        )
        await _run(t.org.id, meeting["id"])
    return t, headers, meeting["id"], company["id"]


# --------------------------------------------------------------------------- #
# settings and the consent statement
# --------------------------------------------------------------------------- #
async def test_the_settings_are_the_admins_and_the_policy_is_the_recorders(client_for) -> None:
    t = await make_tenant("meet-settings")
    headers = await auth_cookie(t.user)
    member = await _member(t, "m@meet-settings.test")
    member_headers = await auth_cookie(member)
    async with client_for(t.host) as c:
        # Defaults where no row exists: the statement is asked for, the transcript is off.
        current = (await c.get("/api/v1/meetings/settings", headers=headers)).json()
        assert current["consent_required"] is True
        assert "transcript" not in current["document_sections"]
        assert "summary" in current["document_sections"]
        assert current["audio_retention_days"] == 30

        saved = await c.put(
            "/api/v1/meetings/settings",
            json={
                "consent_required": False,
                "document_accent_color": "#0f766e",
                "document_footer_text": "Bureau Breik · notulen",
                "document_sections": ["summary", "decisions", "action_items"],
                "ai_instructions": "Schrijf kort. Nooit 'wij' zonder onderwerp.",
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["consent_required"] is False
        assert body["document_sections"] == ["summary", "decisions", "action_items"]
        assert body["ai_instructions"].startswith("Schrijf kort")

        # A member reads the policy and not the settings.
        assert (await c.get("/api/v1/meetings/settings", headers=member_headers)).status_code == 403
        policy = await c.get("/api/v1/meetings/policy", headers=member_headers)
        assert policy.status_code == 200
        assert policy.json() == {"consent_required": False, "audio_retention_days": 30}

        # An unknown chapter is refused by name.
        bad = await c.put(
            "/api/v1/meetings/settings",
            json={"document_sections": ["summary", "jokes"]},
            headers=headers,
        )
        assert bad.status_code == 422


async def test_consent_off_opens_a_recording_nobody_stated_anything_about(client_for) -> None:
    t = await make_tenant("meet-consent-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        refused = await c.post("/api/v1/meetings", json={"title": "x"}, headers=headers)
        assert refused.status_code == 422
        await c.put("/api/v1/meetings/settings", json={"consent_required": False}, headers=headers)
        opened = await c.post("/api/v1/meetings", json={"title": "x"}, headers=headers)
        assert opened.status_code == 201, opened.text
        # Nothing was stated, and the row says so rather than inventing a time.
        assert opened.json()["participants_informed_at"] is None


# --------------------------------------------------------------------------- #
# the notification
# --------------------------------------------------------------------------- #
async def test_the_recorder_is_told_when_the_minutes_are_ready(
    client_for, tmp_path, monkeypatch
) -> None:
    t, headers, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-ready")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(
                select(
                    NotificationEvent.event_type, NotificationEvent.payload, Notification.user_id
                )
                .join(Notification, Notification.event_id == NotificationEvent.id)
                .where(Notification.org_id == t.org.id)
            )
        ).all()
        ready = [r for r in rows if r[0] == "meeting.ready"]
        assert len(ready) == 1, rows
        event_type, payload, user_id = ready[0]
        assert user_id == t.user.id
        assert payload["meeting_id"] == meeting_id
        assert payload["title"] == "Kick-off homepage"
        assert payload["decisions"] == 2 and payload["action_items"] == 2
        # E-mail by this event's own default — the one waiting for it may be away from the desk.
        matrix, _ = await effective_email_matrix(session, t.org.id, t.user.id)
        assert matrix["meeting.ready"].enabled is True
        assert matrix["meeting.ready"].digest == "immediate"
        assert matrix["task.commented"].enabled is False


# --------------------------------------------------------------------------- #
# the transcript as a file
# --------------------------------------------------------------------------- #
def _transcript() -> MeetingTranscript:
    return MeetingTranscript(
        meeting_id=uuid.uuid4(),
        title="Kick-off",
        occurred_at=datetime(2026, 9, 22, 14, 0, tzinfo=UTC),
        speakers={"S1": "Sanne"},
        segments=[
            TranscriptSegment(start=0.0, end=2.5, speaker="S1", text="Goedemorgen."),
            TranscriptSegment(start=62.0, end=65.0, speaker="S2", text="Dag."),
        ],
        text="Goedemorgen. Dag.",
    )


def test_the_transcript_prints_in_four_formats_with_names_resolved() -> None:
    tr = _transcript()
    txt = render_transcript(tr, "txt")
    assert "[0:00] Sanne: Goedemorgen." in txt and "[1:02] S2: Dag." in txt
    md = render_transcript(tr, "md")
    assert md.startswith("# Kick-off") and "**Sanne:** Goedemorgen." in md
    srt = render_transcript(tr, "srt")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nSanne: Goedemorgen.")
    vtt = render_transcript(tr, "vtt")
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.500")


async def test_the_transcript_route_answers_json_and_files(
    client_for, tmp_path, monkeypatch
) -> None:
    t, headers, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-tr")
    async with client_for(t.host) as c:
        as_json = await c.get(f"/api/v1/meetings/{meeting_id}/transcript", headers=headers)
        assert as_json.status_code == 200, as_json.text
        body = as_json.json()
        assert body["title"] == "Kick-off homepage"
        assert [s["speaker"] for s in body["segments"]] == ["S1", "S1", "S2", "S3"]
        assert body["text"].startswith("Goedemorgen allemaal")
        as_srt = await c.get(
            f"/api/v1/meetings/{meeting_id}/transcript", params={"format": "srt"}, headers=headers
        )
        assert as_srt.status_code == 200
        assert as_srt.headers["content-disposition"].endswith('.srt"')
        assert "00:00:03,000 --> 00:00:09,000" in as_srt.text
        bad = await c.get(
            f"/api/v1/meetings/{meeting_id}/transcript", params={"format": "docx"}, headers=headers
        )
        assert bad.status_code == 422


# --------------------------------------------------------------------------- #
# the minutes document
# --------------------------------------------------------------------------- #
async def test_the_document_draws_the_ticked_chapters_and_no_others(
    client_for, tmp_path, monkeypatch
) -> None:
    t, headers, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-doc")
    async with client_for(t.host) as c:
        detail = (await c.get(f"/api/v1/meetings/{meeting_id}", headers=headers)).json()
        # The download's defaults follow the org's, minus what this meeting has nothing for:
        # no open questions were... well, one was; the transcript stays off.
        assert "transcript" not in detail["document_sections"]
        assert "decisions" in detail["document_sections"]
        assert detail["audio_content_type"] == "audio/webm"

        html = await c.get(f"/api/v1/meetings/{meeting_id}/preview", headers=headers)
        assert html.status_code == 200, html.text
        page = html.text
        assert "<!doctype html>" in page.lower()
        assert "Kick-off homepage" in page
        assert "Nova" in page
        # Decisions with their evidence, the unverified one flagged.
        assert "Homepage gaat vrijdag 3 oktober live" in page
        assert 'class="flag"' in page
        # Action items by side and person; the client's promise under its free-text owner.
        assert "Homepageteksten aanleveren" in page and "Jan (klant)" in page
        # The transcript is not on the page unless asked for.
        assert "Goedemorgen allemaal." not in page

        with_transcript = await c.get(
            f"/api/v1/meetings/{meeting_id}/preview",
            params={"sections": "summary,transcript"},
            headers=headers,
        )
        assert with_transcript.status_code == 200
        assert "Goedemorgen allemaal." in with_transcript.text
        assert "Homepageteksten aanleveren" not in with_transcript.text

        unknown = await c.get(
            f"/api/v1/meetings/{meeting_id}/preview", params={"sections": "jokes"}, headers=headers
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["details"]["unknown"] == ["jokes"]


async def test_the_document_prints_pasted_images_names_and_sides(
    client_for, tmp_path, monkeypatch
) -> None:
    """An image pasted into the minutes prints as bytes — only when it is this meeting's own
    file; a transcript line reads as a person, never as the provider's ``S2``; the roster is
    grouped under the agency's and the client's names; and nobody is "recorded by"."""
    t, headers, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-img")
    async with client_for(t.host) as c:
        ours = await c.post(
            "/api/v1/files",
            params={"entity_type": "meeting", "entity_id": meeting_id, "inline": "true"},
            files={"file": ("shot.png", _PNG, "image/png")},
            headers=headers,
        )
        assert ours.status_code == 201, ours.text
        foreign = await c.post(
            "/api/v1/files",
            files={"file": ("other.png", _PNG + b"x", "image/png")},
            headers=headers,
        )
        detail = (await c.get(f"/api/v1/meetings/{meeting_id}", headers=headers)).json()
        minutes = detail["minutes"]
        minutes["summary"] = (
            f"Zie **schets**: ![schets](file:{ours.json()['id']} =50%) en "
            f"![vreemd](file:{foreign.json()['id']})"
        )
        minutes["decisions"][0]["text"] = "Live op **3 oktober**."
        saved = await c.put(f"/api/v1/meetings/{meeting_id}/minutes", json=minutes, headers=headers)
        assert saved.status_code == 200, saved.text

        page = (
            await c.get(
                f"/api/v1/meetings/{meeting_id}/preview",
                params={"sections": "participants,summary,decisions,transcript"},
                headers=headers,
            )
        ).text
        assert page.count('class="md-img"') == 1
        assert 'src="data:image/png;base64,' in page and 'style="width:50%"' in page
        assert "vreemd" not in page
        assert "<strong>3 oktober</strong>" in page
        assert "Opgenomen door" not in page
        assert 'class="side-title"' in page
        assert not re.search(r'<td class="speaker">S\d', page)
        assert "Spreker " in page


async def test_the_settings_preview_renders_a_sample_and_a_custom_design(client_for) -> None:
    t = await make_tenant("meet-preview")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        shipped = await c.post(
            "/api/v1/meetings/settings/preview",
            json={"document_design": "standard"},
            headers=headers,
        )
        assert shipped.status_code == 200, shipped.text
        assert "Kick-off nieuwe website" in shipped.text
        assert "Sanne de Vries" in shipped.text
        source = await c.get("/api/v1/meetings/settings/designs/standard/source", headers=headers)
        assert source.status_code == 200 and "brand-bar" in source.json()["html"]
        own = await c.post(
            "/api/v1/meetings/settings/preview",
            json={
                "document_design": "custom",
                "document_custom_html": "<h1>Eigen notulen: {{ title }}</h1>{{ summary_html }}",
                "document_custom_css": "h1 { color: red }",
            },
            headers=headers,
        )
        assert own.status_code == 200, own.text
        assert "Eigen notulen: Kick-off nieuwe website" in own.text
        broken = await c.post(
            "/api/v1/meetings/settings/preview",
            json={"document_design": "custom", "document_custom_html": "{% for %}"},
            headers=headers,
        )
        assert broken.status_code == 422
        # The same refusal on save, under the editor rather than at the first download.
        refused = await c.put(
            "/api/v1/meetings/settings",
            json={"document_design": "custom", "document_custom_html": "{% for %}"},
            headers=headers,
        )
        assert refused.status_code == 422


async def test_no_box_prints_past_the_edge_of_the_paper(client_for, tmp_path, monkeypatch) -> None:
    """The reporting rule, one document family over: "it fits" is a measurement."""
    import asyncio

    try:
        from weasyprint import HTML as WeasyHTML
    except OSError:  # pragma: no cover - a dev box without Pango; CI has it
        pytest.skip("WeasyPrint's native libraries are not installed here")

    from app.core.documents.engine import no_network_fetcher

    t, headers, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-edge")
    async with client_for(t.host) as c:
        html = (
            await c.get(
                f"/api/v1/meetings/{meeting_id}/preview",
                params={
                    "sections": "participants,summary,topics,decisions,action_items,"
                    "open_questions,evidence,transcript"
                },
                headers=headers,
            )
        ).text
    document = await asyncio.to_thread(
        lambda: WeasyHTML(string=html, url_fetcher=no_network_fetcher, base_url=None).render()
    )

    def boxes(box):  # noqa: ANN001, ANN202
        yield box
        for child in getattr(box, "children", []) or []:
            yield from boxes(child)

    mm = 96 / 25.4
    edge = (210 - 16) * mm
    over = [
        (str(box.element_tag), round(box.position_x + box.width - edge, 1))
        for page in document.pages
        for box in boxes(page._page_box)  # noqa: SLF001
        if isinstance(getattr(box, "width", None), int | float)
        and isinstance(getattr(box, "position_x", None), int | float)
        and str(getattr(box, "element_tag", "") or "") not in ("", "html", "body")
        and box.position_x + box.width > edge + 0.5
    ]
    # The section heading strips bleed to the paper's edge on purpose (the reporting design's
    # rule); everything else must stay inside the text column.
    assert all(tag == "h2" for tag, _ in over), over
    assert len(document.pages) >= 2  # the transcript is an appendix on its own page


# --------------------------------------------------------------------------- #
# a recording plays on a phone: HTTP ranges
# --------------------------------------------------------------------------- #
async def test_a_served_file_answers_byte_ranges(client_for, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("files-range")
    headers = await auth_cookie(t.user)
    # A PNG with a long tail: the instance's upload allow-list decides the type, and the range
    # arithmetic does not care what the bytes are.
    payload = _PNG + bytes(range(256)) * 4
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/files",
            files={"file": ("clip.png", payload, "image/png")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        file_id = created.json()["id"]
        whole = await c.get(f"/api/v1/files/{file_id}", headers=headers)
        assert whole.status_code == 200
        assert whole.headers["accept-ranges"] == "bytes"
        assert whole.content == payload
        size = len(payload)

        # The probe a media element sends first.
        probe = await c.get(f"/api/v1/files/{file_id}", headers={**headers, "Range": "bytes=0-1"})
        assert probe.status_code == 206, probe.text
        assert probe.content == payload[:2]
        assert probe.headers["content-range"] == f"bytes 0-1/{size}"
        assert probe.headers["content-length"] == "2"

        # A seek into the middle, and an open-ended tail.
        middle = await c.get(
            f"/api/v1/files/{file_id}", headers={**headers, "Range": "bytes=100-199"}
        )
        assert middle.status_code == 206 and middle.content == payload[100:200]
        tail = await c.get(f"/api/v1/files/{file_id}", headers={**headers, "Range": "bytes=1000-"})
        assert tail.status_code == 206 and tail.content == payload[1000:]
        assert tail.headers["content-range"] == f"bytes 1000-{size - 1}/{size}"

        beyond = await c.get(
            f"/api/v1/files/{file_id}", headers={**headers, "Range": "bytes=99999-"}
        )
        assert beyond.status_code == 416
        assert beyond.headers["content-range"] == f"bytes */{size}"


# --------------------------------------------------------------------------- #
# changed in words
# --------------------------------------------------------------------------- #
def _detail(**overrides) -> MeetingDetail:  # noqa: ANN003
    staff = uuid.uuid4()
    contact = uuid.uuid4()
    base = {
        "id": uuid.uuid4(),
        "title": "Kick-off",
        "kind": "physical",
        "source": "microphone",
        "status": "review",
        "occurred_at": datetime(2026, 9, 22, 14, 0, tzinfo=UTC),
        "created_at": datetime(2026, 9, 22, 14, 0, tzinfo=UTC),
        "participants": [
            {"name": "Sanne", "user_id": str(staff), "speaker": "S1"},
            {"name": "Jan", "contact_id": str(contact), "speaker": None},
        ],
        "transcript_text": "we spreken af dat de homepage vrijdag live gaat",
        "minutes": {
            "summary": "Kort.",
            "topics": [{"heading": "Planning", "text": "Vrijdag."}],
            "decisions": [{"text": "Live op vrijdag", "quote": None, "verified": True}],
            "action_items": [
                {"title": "Teksten", "assignee_user_id": str(staff), "create_task": True},
                {"title": "Logo", "owner_label": "Jan (klant)", "create_task": False},
            ],
            "open_questions": ["Wie doet de foto's?"],
        },
    }
    base.update(overrides)
    return MeetingDetail.model_validate(base)


def test_a_revision_is_grounded_field_by_field() -> None:
    from datetime import date

    from app.core.ai.candidates import ParseCandidates

    detail = _detail()
    staff = str(detail.participants[0].user_id)
    contact = str(detail.participants[1].contact_id)
    company = str(uuid.uuid4())
    candidates = ParseCandidates(
        companies=[{"id": company, "name": "Nova"}],
        members=[{"id": staff, "name": "Sanne"}],
    )
    revision = revision_from_call(
        {
            "title": "Kick-off homepage",
            "company_id": company,
            "project_id": str(uuid.uuid4()),  # never shown → dropped
            "update_participants": [{"index": 1, "speaker": "S2"}, {"index": 9, "name": "x"}],
            "add_participants": [{"name": "Piet (leverancier)", "user_id": str(uuid.uuid4())}],
            "summary": "Kick-off; live op vrijdag.",
            "remove_decision_indexes": [0, 7],
            "add_decisions": [
                {
                    "text": "Homepage vrijdag live",
                    "quote": "de homepage vrijdag live gaat",
                    "at": 4,
                },
                {"text": "Verzonnen", "quote": "iets dat niemand zei hier"},
            ],
            "update_action_items": [
                {"index": 1, "owner": f"c:{contact}", "due_date": "2026-09-26"},
                {"index": 0, "owner": "u:" + str(uuid.uuid4())},  # unknown colleague → left alone
            ],
            "add_action_items": [
                {"title": "Foto's plannen", "owner": "n:Piet", "due_date": "1999-01-01"}
            ],
            "remove_open_question_indexes": [0],
            "summary_for_colleague": "Klant gezet, besluit vervangen, logo bij Jan.",
        },
        detail=detail,
        today=date(2026, 9, 22),
        candidates=candidates,
        contact_ids={contact.lower()},
        minutes_editable=True,
    )
    assert revision.fields == {"title": "Kick-off homepage", "company_id": uuid.UUID(company)}
    roster = revision.participants
    assert roster is not None
    assert roster[1].speaker == "S2"
    # An unknown staff id is not a colleague: the person lands by name alone.
    assert roster[2].name == "Piet (leverancier)" and roster[2].user_id is None
    draft = revision.minutes
    assert draft is not None
    assert draft.summary == "Kick-off; live op vrijdag."
    assert [d.text for d in draft.decisions] == ["Homepage vrijdag live", "Verzonnen"]
    assert [d.verified for d in draft.decisions] == [True, False]
    logo = draft.action_items[1]
    assert logo.owner_contact_id == uuid.UUID(contact) and logo.owner_label is None
    assert logo.due_date.isoformat() == "2026-09-26"
    assert draft.action_items[0].assignee_user_id == uuid.UUID(staff)  # untouched
    assert draft.action_items[2].owner_label == "Piet" and draft.action_items[2].due_date is None
    assert draft.open_questions == []
    assert revision.summary.startswith("Klant gezet")


def test_a_confirmed_meeting_keeps_its_minutes_whatever_the_answer_says() -> None:
    from datetime import date

    from app.core.ai.candidates import ParseCandidates

    detail = _detail(status="done")
    revision = revision_from_call(
        {"summary": "Herschreven", "add_open_questions": ["Nog iets?"], "title": "Nieuw"},
        detail=detail,
        today=date(2026, 9, 22),
        candidates=ParseCandidates(),
        contact_ids=set(),
        minutes_editable=False,
    )
    assert revision.minutes is None
    assert revision.fields == {"title": "Nieuw"}


async def test_the_box_applies_the_instruction_as_the_caller(
    client_for, tmp_path, monkeypatch
) -> None:
    t, headers, meeting_id, company_id = await _minuted(
        client_for, tmp_path, monkeypatch, "meet-revise"
    )
    answer = [
        AIEvent(
            kind="tool_call",
            tool_call=ToolCall(
                id="c2",
                name="submit_meeting_changes",
                input={
                    "title": "Kick-off homepage Nova",
                    "kind": "online",
                    "update_participants": [{"index": 0, "speaker": "S2"}],
                    "add_open_questions": ["Wie regelt de fotografie?"],
                    "summary_for_colleague": "Titel en soort aangepast, S2 aan jou gekoppeld.",
                },
            ),
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=500, tokens_out=80),
    ]
    monkeypatch.setattr("app.core.ai.providers.stream_chat", _fake_stream(answer))
    async with client_for(t.host) as c:
        res = await c.post(
            f"/api/v1/meetings/{meeting_id}/ai/revise",
            json={"instruction": "noem het Kick-off homepage Nova, online, S2 ben ik"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert sorted(body["changed"]) == ["kind", "minutes", "participants", "title"]
        assert body["summary"].startswith("Titel en soort")
        meeting = body["meeting"]
        assert meeting["title"] == "Kick-off homepage Nova" and meeting["kind"] == "online"
        assert meeting["participants"][0]["speaker"] == "S2"
        assert "Wie regelt de fotografie?" in meeting["minutes"]["open_questions"]
        # The write is the caller's: the trail names the person, not the model.
        trail = await c.get(
            "/api/v1/activity",
            params={"entity_type": "meeting", "entity_id": meeting_id},
            headers=headers,
        )
        assert trail.status_code == 200
        assert any(
            row["action"] == "updated" and row["actor_name"] and not row["actor_deleted"]
            for row in trail.json()
        )


# --------------------------------------------------------------------------- #
# the curated tools
# --------------------------------------------------------------------------- #
async def test_every_meeting_tool_is_a_read_behind_the_read_key() -> None:
    assert {spec.name for spec in MEETING_MCP_TOOLS} == {
        "meetings.find",
        "meetings.transcript",
        "meetings.minutes",
    }
    for spec in MEETING_MCP_TOOLS:
        assert spec.permission == "meetings.meeting.read"
        assert "read" in spec.tags


async def test_the_tools_answer_the_words_and_the_minutes(
    client_for, tmp_path, monkeypatch
) -> None:
    t, headers, meeting_id, company_id = await _minuted(
        client_for, tmp_path, monkeypatch, "meet-tools"
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(type(t.org), t.org.id)
        ctx = RequestContext(
            user=t.user,
            org=org,
            session=session,
            permissions=PermissionSet.of(["meetings.meeting.read", "companies.company.read"]),
        )
        offered = {spec.name for spec in available_tools(ctx)}
        assert {"meetings.find", "meetings.transcript", "meetings.minutes"} <= offered

        find = next(s for s in MEETING_MCP_TOOLS if s.name == "meetings.find")
        found = await run_tool(ctx, find, {"query": "homepage", "company_id": company_id})
        assert [m["id"] for m in found.data["meetings"]] == [meeting_id]
        assert found.data["meetings"][0]["company"] == "Nova"

        words = next(s for s in MEETING_MCP_TOOLS if s.name == "meetings.transcript")
        transcript = await run_tool(ctx, words, {"meeting_id": meeting_id})
        assert transcript.data["segments"][0]["text"] == "Goedemorgen allemaal."
        assert transcript.data["text"].startswith("Goedemorgen")

        minutes = next(s for s in MEETING_MCP_TOOLS if s.name == "meetings.minutes")
        drafted = await run_tool(ctx, minutes, {"meeting_id": meeting_id})
        assert drafted.data["minutes"]["confirmed"] is False
        sides = {block["side"] for block in drafted.data["minutes"]["action_items"]}
        assert sides == {"agency", "other"}
        assert drafted.sources[0].label == "Kick-off homepage"

        # No read key: not offered, and refused when called anyway.
        none_ctx = RequestContext(
            user=t.user, org=org, session=session, permissions=PermissionSet()
        )
        assert not {s.name for s in available_tools(none_ctx)} & offered
        refused = await run_tool(none_ctx, words, {"meeting_id": meeting_id})
        assert refused.data == {"error": "errors.forbidden"}


async def test_the_document_routes_are_tenant_scoped(client_for, tmp_path, monkeypatch) -> None:
    t, _, meeting_id, _ = await _minuted(client_for, tmp_path, monkeypatch, "meet-scope-a")
    other = await make_tenant("meet-scope-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        for path in ("preview", "transcript", "pdf"):
            res = await c.get(f"/api/v1/meetings/{meeting_id}/{path}", headers=other_headers)
            assert res.status_code == 404, (path, res.text)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await session.scalar(select(Meeting.id).where(Meeting.id == uuid.UUID(meeting_id)))
