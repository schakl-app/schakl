"""Speak a task (#382): transcription, the parse, its grounding, and the composite create.

Provider calls are faked at ``app.core.ai.providers.stream_chat`` and
``app.core.ai.taskdraft.provider_transcribe`` — the two seams the feature goes through — so
these exercise the platform's own rules (§15 permissions, Golden Rule 1 isolation, the #129
grounding discipline) with no network.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.ai.models import AIUsage
from app.core.ai.providers import AIEvent, ToolCall
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant, org_today

_password_hash = PasswordHash.recommended()

SETTINGS_BODY = {
    "provider": "anthropic",
    "api_key": "sk-test-super-secret-123",
    "features": {"task_assist": {"enabled": True}},
}
SPEECH_BODY = {
    **SETTINGS_BODY,
    "speech_provider": "openai",
    "speech_api_key": "sk-speech-secret-456",
    "speech_model": "whisper-1",
}
WEBM = base64.b64encode(b"\x1a\x45\xdf\xa3" + b"\x00" * 64).decode()


def _fake_stream(events: list[AIEvent]):
    async def fake(config, **kwargs) -> AsyncIterator[AIEvent]:  # noqa: ANN001, ANN003
        for event in events:
            yield event

    return fake


def _submit(**fields) -> list[AIEvent]:  # noqa: ANN003
    return [
        AIEvent(
            kind="tool_call",
            tool_call=ToolCall(id="c1", name="submit_task", input=dict(fields)),
        ),
        AIEvent(kind="done", stop_reason="tool_use", tokens_in=5, tokens_out=9),
    ]


def _fake_transcript(text: str, seconds: int = 21):
    async def fake(config, clip, *, language):  # noqa: ANN001
        from app.core.ai.transcribe import Transcript

        return Transcript(text=text, seconds=seconds)

    return fake


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #
async def test_transcribe_returns_the_words_and_meters_seconds(client_for, monkeypatch) -> None:
    """The clip comes back as text and the cost lands in the *audio* column (#246's rule).

    Nothing is stored and nothing is created: the transcript exists so the speaker can read it
    before it is parsed, which is the only place a misheard client name can be caught.
    """
    t = await make_tenant("dictate-ok")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.taskdraft.provider_transcribe",
        _fake_transcript("maak een taak voor Jansen, homepageteksten, uiterlijk vrijdag", 21),
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=headers)
        res = await c.post(
            "/api/v1/ai/tasks/transcribe",
            json={"audio": WEBM, "language": "nl-NL"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["text"].startswith("maak een taak voor Jansen")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(select(AIUsage).where(AIUsage.org_id == t.org.id))
        ).scalars()
        audio = [r for r in rows if r.audio_seconds]
        assert len(audio) == 1
        assert audio[0].audio_seconds == 21
        assert audio[0].feature == "task_assist"
        # Seconds are their own unit: folding them into tokens would corrupt both budgets.
        assert audio[0].tokens_in == 0 and audio[0].tokens_out == 0


async def test_transcribe_requires_being_able_to_create_a_task(client_for, monkeypatch) -> None:
    """``ai.use`` is the enumerable route permission; the *service* asks for
    ``tasks.task.create`` (#246's split, one record over).

    A microphone that bills the tenant's audio budget must not be reachable by someone who
    could not do anything with the result.
    """
    t = await make_tenant("dictate-perm")
    headers = await auth_cookie(t.user)
    monkeypatch.setattr(
        "app.core.ai.taskdraft.provider_transcribe", _fake_transcript("iets")
    )
    async with async_session_maker() as session:
        reader = User(
            id=uuid.uuid4(),
            email="reader-dictate@example.com",
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

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SPEECH_BODY, headers=headers)
        refused = await c.post(
            "/api/v1/ai/tasks/transcribe", json={"audio": WEBM}, headers=reader_headers
        )
        assert refused.status_code == 403, refused.text


# --------------------------------------------------------------------------- #
# The parse
# --------------------------------------------------------------------------- #
async def test_parse_fills_the_whole_form(client_for, monkeypatch) -> None:
    """The vocabulary is the task form, not #327's six fields — because the words are a
    colleague's own and a human presses the button (see ``taskdraft``'s module docstring)."""
    t = await make_tenant("dictate-parse")
    headers = await auth_cookie(t.user)
    due = org_today().isoformat()
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        company = await c.post("/api/v1/companies", json={"name": "Jansen"}, headers=headers)
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        label = await c.post(
            "/api/v1/tasks/labels", json={"name": "SEO", "color": "sky"}, headers=headers
        )
        assert label.status_code == 201, label.text
        label_id = label.json()["id"]

        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _submit(
                    title="Homepageteksten herschrijven",
                    description="Klant wil een informele toon.",
                    due_date=due,
                    priority="high",
                    status="in_progress",
                    company_id=company_id,
                    assignee_user_id=str(t.user.id),
                    label_ids=[label_id],
                    allocated_minutes=180,
                    checklist_title="Aanpak",
                    checklist_items=[
                        {"title": "Concept schrijven"},
                        {"title": "Review met de klant", "description": "Bel Marieke"},
                        {"title": "In WordPress zetten"},
                    ],
                    links=[{"url": "jansen.nl/home", "title": "Huidige pagina"}],
                    requires_interaction=True,
                    visible_to_client=True,
                )
            ),
        )
        parsed = await c.post(
            "/api/v1/ai/tasks/parse",
            json={"text": "maak een taak voor Jansen …"},
            headers=headers,
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["title"] == "Homepageteksten herschrijven"
        assert body["due_date"] == due
        assert body["priority"] == "high"
        assert body["status"] == "in_progress"
        assert body["company_id"] == company_id
        assert body["assignee_user_id"] == str(t.user.id)
        assert body["label_ids"] == [label_id]
        assert body["allocated_minutes"] == 180
        assert [i["title"] for i in body["checklist_items"]] == [
            "Concept schrijven",
            "Review met de klant",
            "In WordPress zetten",
        ]
        # A bare host is completed rather than dropped — the speaker said it out loud.
        assert body["links"] == [{"url": "https://jansen.nl/home", "title": "Huidige pagina"}]
        assert body["requires_interaction"] is True
        assert body["visible_to_client"] is True
        assert body["truncated"] is False


async def test_parse_drops_every_id_it_was_not_shown(client_for, monkeypatch) -> None:
    """#129's rule, per type.

    ``assignee_user_id`` and ``label_ids`` are grounded against their **own** evidence sets
    rather than the shared pool the time parse uses: a project id offered as a company fails
    the write anyway, while another entity's id in ``assignee_user_id`` is a real user id from
    the same space. A misheard name must come back empty, never as somebody else.
    """
    t = await make_tenant("dictate-ground")
    other = await make_tenant("dictate-ground-other")
    headers = await auth_cookie(t.user)
    async with client_for(other.host) as c:
        foreign = await c.post(
            "/api/v1/companies",
            json={"name": "Andermans klant"},
            headers=await auth_cookie(other.user),
        )
        assert foreign.status_code == 201
        foreign_id = foreign.json()["id"]

    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                _submit(
                    title="Iets",
                    company_id=foreign_id,
                    project_id=str(uuid.uuid4()),
                    # The *other* org's owner: a real user, and not one this org was shown.
                    assignee_user_id=str(other.user.id),
                    label_ids=[str(uuid.uuid4())],
                    status="niet_bestaand",
                )
            ),
        )
        parsed = await c.post(
            "/api/v1/ai/tasks/parse", json={"text": "iets"}, headers=headers
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["company_id"] is None
        assert body["project_id"] is None
        assert body["assignee_user_id"] is None
        assert body["label_ids"] == []
        # A status is a slug, so membership in the org's own vocabulary is its grounding.
        assert body["status"] is None


async def test_parse_leaves_unsaid_things_null(client_for, monkeypatch) -> None:
    """Both booleans are tri-state (#284's ``billable`` lesson, one module over).

    ``False`` and "the speaker said nothing" are different facts, and only a ``None`` lets the
    form keep the platform's own default rather than recording a decision nobody made.
    """
    t = await make_tenant("dictate-null")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(title="Bellen"))
        )
        body = (
            await c.post("/api/v1/ai/tasks/parse", json={"text": "bellen"}, headers=headers)
        ).json()
        assert body["requires_interaction"] is None
        assert body["visible_to_client"] is None
        assert body["priority"] is None
        assert body["due_date"] is None
        assert body["checklist_items"] == []


async def test_parse_pin_is_a_default_the_words_override(client_for, monkeypatch) -> None:
    """A screen that already names a client supplies it, and a spoken client still wins.

    The other way round the draft would silently disagree with the words it was made from,
    which is the failure nobody catches — a correction on the form takes one click.
    """
    t = await make_tenant("dictate-pin")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        pinned = await c.post("/api/v1/companies", json={"name": "Pin"}, headers=headers)
        spoken = await c.post("/api/v1/companies", json={"name": "Jansen"}, headers=headers)
        pinned_id, spoken_id = pinned.json()["id"], spoken.json()["id"]

        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat", _fake_stream(_submit(title="A"))
        )
        quiet = await c.post(
            "/api/v1/ai/tasks/parse",
            json={"text": "iets doen", "company_id": pinned_id},
            headers=headers,
        )
        assert quiet.json()["company_id"] == pinned_id

        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(_submit(title="A", company_id=spoken_id)),
        )
        loud = await c.post(
            "/api/v1/ai/tasks/parse",
            json={"text": "iets doen voor Jansen", "company_id": pinned_id},
            headers=headers,
        )
        assert loud.json()["company_id"] == spoken_id


async def test_parse_reports_a_truncated_answer_instead_of_raising(
    client_for, monkeypatch
) -> None:
    """"A truncated answer is not an empty one" (docs/AI.md) — and here it is also not an error.

    #327 raises, correctly: nobody is waiting on a worker and a half-read email is better not
    written. Here the speaker *is* waiting, over words they can still see, so a partial draft
    is handed over with a flag and the form says the plan may be short.
    """
    t = await make_tenant("dictate-trunc")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        monkeypatch.setattr(
            "app.core.ai.providers.stream_chat",
            _fake_stream(
                [
                    AIEvent(
                        kind="tool_call",
                        tool_call=ToolCall(
                            id="c1",
                            name="submit_task",
                            input={"title": "Halve taak"},
                            incomplete=True,
                        ),
                    ),
                    AIEvent(kind="done", stop_reason="length", tokens_in=4, tokens_out=999),
                ]
            ),
        )
        res = await c.post(
            "/api/v1/ai/tasks/parse", json={"text": "een lange dictatie"}, headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["title"] == "Halve taak"
        assert res.json()["truncated"] is True


async def test_parse_is_refused_without_the_create_permission(client_for, monkeypatch) -> None:
    """Same split as the transcription: the route declares ``ai.use``, the service asks whether
    this caller could create the task the draft is for."""
    t = await make_tenant("dictate-parse-perm")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        reader = User(
            id=uuid.uuid4(),
            email="reader-parse@example.com",
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
        "app.core.ai.providers.stream_chat", _fake_stream(_submit(title="Iets"))
    )
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        refused = await c.post(
            "/api/v1/ai/tasks/parse", json={"text": "iets"}, headers=reader_headers
        )
        assert refused.status_code == 403, refused.text


# --------------------------------------------------------------------------- #
# The composite create
# --------------------------------------------------------------------------- #
async def test_create_carries_its_checklist_links_and_labels(client_for) -> None:
    """One call, one transaction (#382).

    Everything goes through the service's own methods, so the inline checklist is validated,
    recorded on the trail and permission-checked exactly as one added from the card would be —
    which is the whole reason this is a wider ``TaskCreate`` and not a second endpoint.
    """
    t = await make_tenant("composite-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        label = await c.post(
            "/api/v1/tasks/labels", json={"name": "SEO", "color": "sky"}, headers=headers
        )
        label_id = label.json()["id"]
        created = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Homepageteksten herschrijven",
                "checklist": {
                    "title": "Aanpak",
                    "items": [
                        {"title": "Concept schrijven"},
                        {"title": "Review", "description": "met Marieke"},
                    ],
                },
                "links": [{"url": "https://jansen.nl/home", "title": "Huidige pagina"}],
                "label_ids": [label_id],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        detail = await c.get(
            f"/api/v1/tasks/{created.json()['id']}", headers=headers
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert len(body["checklists"]) == 1
        assert body["checklists"][0]["title"] == "Aanpak"
        assert [i["title"] for i in body["checklists"][0]["items"]] == [
            "Concept schrijven",
            "Review",
        ]
        assert body["links"][0]["url"] == "https://jansen.nl/home"
        assert [row["id"] for row in body["labels"]] == [label_id]
        # The trail saw the checklist and the labels. Items are *not* recorded one by one —
        # they are written the way a template copy writes them, which has never recorded a line
        # per item either, and "twelve checklist_item_added rows on a create" is a trail nobody
        # reads. (`add_link` records nothing at all: a pre-existing gap in the module's own
        # trail, not one this create introduces or should paper over.)
        actions = {row["action"] for row in body["activities"]}
        assert {"created", "checklist_created", "updated"} <= actions


async def test_create_without_the_composite_fields_is_unchanged(client_for) -> None:
    """Every existing caller keeps working: absent means absent, and no empty checklist is
    invented for a task that did not ask for one."""
    t = await make_tenant("composite-none")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post("/api/v1/tasks", json={"title": "Gewoon"}, headers=headers)
        assert created.status_code == 201, created.text
        detail = (
            await c.get(f"/api/v1/tasks/{created.json()['id']}", headers=headers)
        ).json()
        assert detail["checklists"] == []
        assert detail["links"] == []
        assert detail["labels"] == []


async def test_create_checklist_without_a_title_borrows_the_task_s(client_for) -> None:
    """``add_checklist`` refuses an untitled checklist, and an i18n key cannot help — this is
    stored tenant data and the writer's locale is not the reader's. The task's own title is the
    only string here that is neither invented nor English (``tasks.system``'s fallback)."""
    t = await make_tenant("composite-untitled")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks",
            json={"title": "Oplevering", "checklist": {"items": [{"title": "Stap"}]}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        detail = (
            await c.get(f"/api/v1/tasks/{created.json()['id']}", headers=headers)
        ).json()
        assert detail["checklists"][0]["title"] == "Oplevering"


async def test_composite_create_never_crosses_a_tenant(client_for) -> None:
    """Golden Rule 1: a label id from another org is a 404, not a silent skip and certainly
    not a link."""
    t = await make_tenant("composite-iso-a")
    other = await make_tenant("composite-iso-b")
    async with client_for(other.host) as c:
        foreign = await c.post(
            "/api/v1/tasks/labels",
            json={"name": "Vreemd", "color": "sky"},
            headers=await auth_cookie(other.user),
        )
        foreign_label = foreign.json()["id"]

    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/tasks",
            json={"title": "Iets", "label_ids": [foreign_label]},
            headers=await auth_cookie(t.user),
        )
        assert refused.status_code == 404, refused.text
