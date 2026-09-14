"""The e-mail intake address (``taak@bureau.nl``): parsing, the feed hook, the parked queue."""

from __future__ import annotations

import base64
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.mailbox.intake import IntakeOutcome, merge_links
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from app.integrations.google.gmail.service import poll_connection
from app.integrations.google.models import GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_GMAIL
from app.modules.interactions.models import Interaction
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.tasks import intake
from app.modules.tasks.models import Task, TaskActivity, TaskIntakeMessage
from tests.conftest import auth_cookie, make_tenant, org_today
from tests.test_google_gmail import _message, _StubGmail

# --------------------------------------------------------------------------- #
# Pure parsing
# --------------------------------------------------------------------------- #


def test_subject_prefixes_and_bracketed_client() -> None:
    assert intake.clean_subject("Fwd: RE: [Nova Fietsen] SSL verlengen") == (
        "SSL verlengen",
        "Nova Fietsen",
    )
    assert intake.clean_subject("FW: Offerte") == ("Offerte", None)
    assert intake.clean_subject(None) == ("", None)


def test_directives_are_read_off_the_top_and_taken_out_of_the_body() -> None:
    body = (
        "klant: Nova Fietsen\n"
        "Voor: Lotte\n"
        "deadline: vrijdag\n"
        "labels: seo, spoed\n"
        "prioriteit: hoog\n"
        "\n"
        "Even de DNS nakijken, zie hieronder.\n"
        "\n"
        "---------- Forwarded message ---------\n"
        "From: Sander <sander@nova.nl>\n"
        "Subject: DNS\n"
        "\n"
        "Kunnen jullie de MX records controleren?"
    )
    draft = intake.parse_intake("Fwd: DNS", body)
    assert draft.title == "DNS"
    assert draft.client_hint == "Nova Fietsen"
    assert draft.assignee_hint == "Lotte"
    assert draft.due_hint == "vrijdag"
    assert draft.label_hints == ["seo", "spoed"]
    assert draft.priority == "high"
    assert draft.own_text == "Even de DNS nakijken, zie hieronder."
    assert draft.forwarded_text.startswith("---------- Forwarded message")
    assert draft.addresses == ["sander@nova.nl"]
    # The description keeps the words and drops the directives.
    assert "klant:" not in draft.body and "Kunnen jullie" in draft.body


def test_a_quoted_reply_counts_as_forwarded() -> None:
    draft = intake.parse_intake(
        "Re: Logo", "Zie onder.\n\n> Op 1 sep schreef Klant <k@client.nl>:\n> Logo bijgevoegd"
    )
    assert draft.own_text == "Zie onder."
    assert draft.addresses == ["k@client.nl"]


def test_due_dates_as_people_type_them() -> None:
    today = date(2026, 9, 9)  # a Wednesday
    parse = lambda value: intake.parse_due(value, today=today)  # noqa: E731
    assert parse("2026-10-01") == date(2026, 10, 1)
    assert parse("01-10-2026") == date(2026, 10, 1)
    assert parse("1/10") == date(2026, 10, 1)
    assert parse("vrijdag") == date(2026, 9, 11)
    assert parse("woensdag") == today
    assert parse("volgende week woensdag") == date(2026, 9, 16)
    assert parse("morgen") == date(2026, 9, 10)
    assert parse("+3") == date(2026, 9, 12)
    assert parse("over 2 weken") == date(2026, 9, 23)
    assert parse("eind van de week") == date(2026, 9, 11)
    assert parse("ooit") is None


def test_merge_links_files_the_interaction_onto_the_task() -> None:
    task_id, company_id = uuid.uuid4(), uuid.uuid4()
    outcome = IntakeOutcome(status="created", links={"task_id": task_id, "company_id": company_id})
    merged = merge_links({"company_id": None, "contact_id": uuid.uuid4()}, outcome)
    assert merged["task_id"] == task_id and merged["task_ids"] == [task_id]
    assert merged["company_id"] == company_id
    assert merge_links({"company_id": company_id}, None) == {"company_id": company_id}


# --------------------------------------------------------------------------- #
# The feed hook, against the scripted Gmail
# --------------------------------------------------------------------------- #

INTAKE = "taak@agency.nl"


def _stub_acting_as(stub):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


async def _seed(tenant, *, history_id: str = "5") -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id, gmail_enabled=True, gmail_approval_mode="approval_required"
            )
        )
        connection = GoogleConnection(
            org_id=tenant.org.id,
            user_id=tenant.user.id,
            google_sub="sub",
            email="me@agency.nl",
            scopes=["openid", "email", SCOPE_GMAIL],
            refresh_token_encrypted=encrypt("rt"),
            gmail_sync_enabled=True,
            gmail_history_id=history_id,
        )
        session.add(connection)
        await session.commit()
        return connection.id


async def _poll(tenant, connection_id, stub, monkeypatch) -> int:
    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        logged = await poll_connection(session, tenant.org, connection)
        await session.commit()
        return logged


def _sent(
    message_id: str,
    *,
    subject: str,
    body: str,
    cc: str | None = None,
    to: str = INTAKE,
    sender: str = "Ik <me@agency.nl>",
) -> dict:
    message = _message(
        message_id,
        sender=sender,
        to=to,
        cc=cc,
        subject=subject,
        labels=["SENT"],
        thread=f"thr-{message_id}",
        body_text=body,
    )
    return message


async def _configure(c, headers, **overrides) -> None:
    res = await c.put(
        "/api/v1/tasks/settings", json={"intake_address": INTAKE, **overrides}, headers=headers
    )
    assert res.status_code == 200, res.text


async def _rows(org_id, model):
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return (await session.execute(select(model))).scalars().all()


async def test_a_mail_to_the_task_address_becomes_the_senders_task(
    client_for, monkeypatch, tmp_path
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("intake-basic")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()

        message = _sent(
            "msg-1",
            subject="Fwd: [Nova Fietsen] SSL verlengen",
            body=(
                "prioriteit: hoog\n\nGraag voor het weekend regelen.\n\n"
                "---------- Forwarded message ---------\nFrom: Sander <sander@nova.nl>\n\n"
                "Ons certificaat verloopt."
            ),
        )
        message["payload"] = {
            "headers": message["payload"]["headers"],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": message["payload"]["body"]["data"]}},
                {
                    "filename": "cert.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-1", "size": 9},
                },
            ],
        }
        stub = _StubGmail(history=["msg-1"], messages={"msg-1": message}, history_id="9000")
        stub.messages["att-1"] = {
            "size": 9,
            "data": base64.urlsafe_b64encode(b"%PDF-fake").decode(),
        }
        assert await _poll(t, connection_id, stub, monkeypatch) == 1

        tasks = await _rows(t.org.id, Task)
        assert len(tasks) == 1
        task = tasks[0]
        assert task.title == "SSL verlengen"
        assert task.company_id == uuid.UUID(company["id"])
        assert task.assignee_user_id == t.user.id
        assert task.priority == "high"
        # No deadline in the mail: the org's today plus the default (one day).
        assert task.due_date == org_today() + timedelta(days=1)
        assert "Graag voor het weekend regelen." in (task.description or "")
        assert "prioriteit:" not in (task.description or "")

        stored = await _rows(t.org.id, StoredFile)
        assert [(f.filename, f.entity_type, f.entity_id) for f in stored] == [
            ("cert.pdf", "task", task.id)
        ]

        receipts = await _rows(t.org.id, TaskIntakeMessage)
        assert len(receipts) == 1 and receipts[0].status == "created"
        assert receipts[0].task_id == task.id and receipts[0].sender_user_id == t.user.id

        # The trail names the sender, not the system, and says it came by mail.
        trail = await _rows(t.org.id, TaskActivity)
        created = next(a for a in trail if a.action == "created")
        assert created.actor_user_id == t.user.id and created.payload.get("via") == "email"

        # The sender heard about it once, through the ordinary notification system, and did
        # not *also* get "assigned you" for their own mail.
        events = {e.id: e for e in await _rows(t.org.id, NotificationEvent)}
        inbox = await _rows(t.org.id, Notification)
        kinds = sorted(
            events[n.event_id].event_type
            for n in inbox
            if n.user_id == t.user.id and events[n.event_id].event_type.startswith("task.")
        )
        assert kinds == ["task.intake_created"]
        # No timeline row: colleague-to-colleague chatter stays off the timeline.
        assert await _rows(t.org.id, Interaction) == []

        # The settings screen can say when the last one arrived.
        settings = (await c.get("/api/v1/tasks/settings", headers=headers)).json()
        assert settings["intake_received_count"] == 1
        assert settings["intake_last_received_at"] is not None

        # The same message polled again (a colleague's copy, a re-offered history page) is one
        # task, decided by the database, not a second one.
        assert await _poll(t, connection_id, stub, monkeypatch) == 0
        assert len(await _rows(t.org.id, Task)) == 1


async def test_a_mail_with_no_recognisable_client_parks_for_its_sender(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("intake-park")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers, intake_default_due_days=3)
        company = (
            await c.post("/api/v1/companies", json={"name": "Later Gekozen BV"}, headers=headers)
        ).json()
        message = _sent(
            "msg-p", subject="Nieuwsbrief opzetten", body="Voor de nieuwe klant, details volgen."
        )
        stub = _StubGmail(history=["msg-p"], messages={"msg-p": message}, history_id="9100")
        assert await _poll(t, connection_id, stub, monkeypatch) == 0
        assert await _rows(t.org.id, Task) == []
        receipts = await _rows(t.org.id, TaskIntakeMessage)
        assert len(receipts) == 1
        row = receipts[0]
        assert row.status == "needs_client" and row.reason == "no_client"
        events = [
            e for e in await _rows(t.org.id, NotificationEvent) if e.event_type.startswith("task.")
        ]
        assert [e.event_type for e in events] == ["task.intake_parked"]
        assert events[0].entity_type == "task_intake" and events[0].entity_id == row.id

        # The strip and the list are the sender's own.
        assert (await c.get("/api/v1/tasks/intake/summary", headers=headers)).json() == {
            "needs_client": 1
        }
        listed = (await c.get("/api/v1/tasks/intake", headers=headers)).json()
        assert [r["id"] for r in listed] == [str(row.id)]

        # Finishing by hand: name the client, and the task is created as the caller.
        res = await c.post(
            f"/api/v1/tasks/intake/{row.id}/create",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        task = res.json()
        assert task["title"] == "Nieuwsbrief opzetten"
        assert task["company_id"] == company["id"]
        assert task["due_date"] == (org_today() + timedelta(days=3)).isoformat()
        assert task["assignee_user_id"] == str(t.user.id)
        # Decided: a second press is refused, and the strip is empty.
        res = await c.post(
            f"/api/v1/tasks/intake/{row.id}/create",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert res.status_code == 409
        assert (await c.get("/api/v1/tasks/intake/summary", headers=headers)).json() == {
            "needs_client": 0
        }

    # Another tenant sees none of it (RLS + the sender filter).
    other = await make_tenant("intake-park-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as cb:
        assert (await cb.get("/api/v1/tasks/intake", headers=other_headers)).json() == []
        assert (
            await cb.post(
                f"/api/v1/tasks/intake/{row.id}/create",
                json={"company_id": company["id"]},
                headers=other_headers,
            )
        ).status_code == 404


async def test_an_outsider_mailing_the_address_creates_nothing(client_for, monkeypatch) -> None:
    """A copy arriving in a connected mailbox from somebody who is not a member: no row."""
    t = await make_tenant("intake-outsider")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
    message = _message(
        "msg-x", sender="spam@elders.nl", to=INTAKE, subject="Gratis SEO", body_text="Klik hier."
    )
    stub = _StubGmail(history=["msg-x"], messages={"msg-x": message}, history_id="9200")
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    assert await _rows(t.org.id, TaskIntakeMessage) == []
    assert await _rows(t.org.id, Task) == []


async def test_copying_the_address_on_a_client_thread_logs_both(client_for, monkeypatch) -> None:
    """A client writes to me with ``taak@`` in Cc: a task for their client *and* a pending
    contact moment already filed onto that task."""
    t = await make_tenant("intake-cc")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Klant",
                "email": "klant@client.nl",
                "company_ids": [company["id"]],
            },
            headers=headers,
        )
    # Outbound: I reply to the client and copy the task address, with a directive on top.
    message = _sent(
        "msg-c",
        subject="Re: Logo aanleveren",
        body=(
            "deadline: 2030-01-15\n\nWe wachten op het logo.\n\n"
            "Op 1 sep schreef Klant <klant@client.nl>:\n> Sturen jullie het logo?"
        ),
        to="klant@client.nl",
        cc=INTAKE,
    )
    stub = _StubGmail(history=["msg-c"], messages={"msg-c": message}, history_id="9300")
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    tasks = await _rows(t.org.id, Task)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.company_id == uuid.UUID(company["id"])
    assert task.due_date == date(2030, 1, 15)
    rows = await _rows(t.org.id, Interaction)
    assert len(rows) == 1
    assert rows[0].status == "pending" and rows[0].task_id == task.id
    assert rows[0].company_id == uuid.UUID(company["id"])
    # The row keeps the real headers — whose mail it is stays a header fact.
    assert "klant@client.nl" in [p["email"] for p in rows[0].participants]


async def test_the_model_fills_only_what_the_words_left_blank(client_for, monkeypatch) -> None:
    t = await make_tenant("intake-ai")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
        await c.post("/api/v1/companies", json={"name": "Andere Klant"}, headers=headers)

    async def _available(ctx):  # noqa: ANN001
        return True

    captured: dict = {}

    async def _complete(  # noqa: ANN001
        self, feature, *, system, messages, tools, force_tool=None, **kw
    ):
        from app.core.ai.providers import ToolCall

        captured["feature"] = feature
        captured["document"] = messages[0].content
        return "", [
            ToolCall(
                id="c1",
                name="submit_intake_task",
                input={
                    "company_id": company["id"],
                    "assignee_user_id": str(uuid.uuid4()),  # not on the shortlist: dropped
                    "due_date": "2027-01-15",  # the sender said nothing: filled
                    "summary": "Nieuwsbrief-template bouwen in Mailchimp.",
                    "checklist_items": [{"title": "Template"}, {"title": "Testmail"}],
                    "links": [
                        {"url": "https://nova.nl/huisstijl"},
                        {"url": "https://invented.example"},  # not in the mail: dropped
                    ],
                },
            )
        ]

    async def _flush(self, feature):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.modules.tasks.intake_ai.available", _available)
    monkeypatch.setattr("app.core.ai.service.AIService.complete", _complete)
    monkeypatch.setattr("app.core.ai.service.AIService.flush_usage", _flush)
    monkeypatch.setattr(
        "app.core.ai.service.AIService.truncated", property(lambda self: False)
    )

    message = _sent(
        "msg-ai",
        subject="Nieuwsbrief voor Nova",
        body="Kun je de nieuwsbrief opzetten? Huisstijl: https://nova.nl/huisstijl",
    )
    stub = _StubGmail(history=["msg-ai"], messages={"msg-ai": message}, history_id="9400")
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    assert captured["feature"] == "task_intake"
    assert "Nieuwsbrief" in captured["document"]
    tasks = await _rows(t.org.id, Task)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.company_id == uuid.UUID(company["id"])
    assert task.assignee_user_id == t.user.id  # the invented id was dropped; the sender holds it
    assert task.due_date == date(2027, 1, 15)
    assert (task.description or "").startswith("Nieuwsbrief-template bouwen")
    receipts = await _rows(t.org.id, TaskIntakeMessage)
    assert receipts[0].hints["by_model"] == ["company", "due_date"]
    async with client_for(t.host) as c:
        detail = (await c.get(f"/api/v1/tasks/{task.id}", headers=headers)).json()
        assert [i["title"] for i in detail["checklists"][0]["items"]] == ["Template", "Testmail"]
        assert [link["url"] for link in detail["links"]] == ["https://nova.nl/huisstijl"]


async def test_settings_validate_and_switch_off(client_for) -> None:
    t = await make_tenant("intake-settings")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/tasks/settings", headers=headers)).json() == {
            "intake_address": None,
            "intake_default_due_days": 1,
            "intake_last_received_at": None,
            "intake_received_count": 0,
        }
        assert (
            await c.put("/api/v1/tasks/settings", json={"intake_address": "nope"}, headers=headers)
        ).status_code == 422
        await _configure(c, headers, intake_default_due_days=2)
        saved = (await c.get("/api/v1/tasks/settings", headers=headers)).json()
        assert saved["intake_address"] == INTAKE
        # An explicit null switches it off; an absent field leaves the days alone.
        res = await c.put(
            "/api/v1/tasks/settings", json={"intake_address": None}, headers=headers
        )
        assert res.json()["intake_address"] is None and res.json()["intake_default_due_days"] == 2
