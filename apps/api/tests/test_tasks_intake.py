"""The e-mail intake address (``taak@bureau.nl``): parsing, the feed hook, the parked queue."""

from __future__ import annotations

import base64
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.mailbox.intake import IntakeOutcome, merge_links
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from app.integrations.google.gmail.service import poll_connection
from app.integrations.google.models import GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_GMAIL
from app.modules.interactions.models import Interaction, InteractionTask
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.tasks import intake
from app.modules.tasks.intake_ai import calendar_line
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
    # "The coming one", as people write it — and a day with its month name.
    assert parse("as vrijdag") == date(2026, 9, 11)
    assert parse("a.s. vrijdag") == parse("vrijdag a.s.") == parse("aanstaande vrijdag")
    assert parse("voor woensdag") == today
    assert parse("1 oktober") == date(2026, 10, 1)
    assert parse("1 okt 2026") == date(2026, 10, 1)
    assert parse("12 januari") == date(2027, 1, 12)  # already passed this year: next year's


def test_a_deadline_stated_in_the_running_text_is_read() -> None:
    """The live mail: "met deadline as vrijdag" on a Monday landed on the Thursday, because the
    directive parser only read ``deadline:`` lines and the model did the weekday arithmetic."""
    phrase = intake.due_phrase
    assert phrase("Maak een taak aan voor Stan met deadline as vrijdag. Hij moet") == "as vrijdag"
    assert phrase("Graag uiterlijk 1 oktober opleveren.") == "1 oktober"
    assert phrase("Dit moet voor woensdag af zijn") == "woensdag"
    assert phrase("Bel de klant, deadline: 30-09") == "30-09"
    # "voor" is also "for": a keyword whose words name no date names nothing.
    assert phrase("Niets over een datum hier voor Stan of project X") is None
    monday = date(2026, 9, 14)
    draft = intake.parse_intake("Fwd: Dashboard", "Maak een taak aan met deadline as vrijdag.")
    assert intake.parse_due(draft.due_hint, today=monday) == date(2026, 9, 18)
    # And the model is told the weekday, and every day it may resolve a word against.
    line = calendar_line(monday, datetime(2026, 9, 14, 17, 56, tzinfo=UTC))
    assert line.startswith("Today is Monday 2026-09-14, local time 17:56.")
    assert "Fri 2026-09-18" in line


def test_the_signature_is_cut_and_the_forward_headers_are_read() -> None:
    zone = ZoneInfo("Europe/Amsterdam")
    # The HTML-derived body: escaped marker, bold names, addresses wrapped in mailto links.
    body = (
        "Kijk ook de doorgestuurde mail.\n\nMet vriendelijke groet,\n\n**Stan**\n\n"
        "**T. 0113** [stan@breik.nl](mailto:stan@breik.nl)\n\n"
        "\\---------- Forwarded message ---------  \n"
        "From: **Luka Abazovic | breik.** <[luka@breik.nl](mailto:luka@breik.nl)\\>  \n"
        "Date: Fri, 11 Sept 2026 at 10:35  \n"
        "Subject: Dashboard module uitleg  \n"
        "To: Stan Marcusse <[stan@breik.nl](mailto:stan@breik.nl)\\>\n\n"
        "Ik hoop dat het duidelijk is!\n\nHartelijke groet,\n\n**Luka**"
    )
    draft = intake.parse_intake("Fwd: Dashboard module uitleg", body)
    assert draft.own_text == "Kijk ook de doorgestuurde mail."
    assert draft.forwarded_text.startswith("\\---------- Forwarded message")
    mail = intake.parse_forwarded(draft.forwarded_text, zone=zone)
    assert (mail.from_name, mail.from_email) == ("Luka Abazovic | breik.", "luka@breik.nl")
    assert mail.to == [("Stan Marcusse", "stan@breik.nl")]
    assert mail.subject == "Dashboard module uitleg"
    assert mail.sent_at == datetime(2026, 9, 11, 10, 35, tzinfo=zone)
    assert mail.body == "Ik hoop dat het duidelijk is!\n\nHartelijke groet,\n\n**Luka**"
    assert not mail.quoted
    # Outlook's Dutch header block, and a quoted reply (which names its sender and nothing
    # else, and is marked as a quote — the thread's previous turn, not a forward).
    outlook = intake.parse_forwarded(
        "Van: Klant <k@client.nl>\nVerzonden: vrijdag 11 september 2026 09:15\n"
        "Aan: Ik <me@agency.nl>\nOnderwerp: Offerte\n\nGraag een offerte.",
        zone=zone,
    )
    assert outlook.from_email == "k@client.nl" and outlook.subject == "Offerte"
    assert outlook.sent_at == datetime(2026, 9, 11, 9, 15, tzinfo=zone)
    assert outlook.body == "Graag een offerte."
    quoted = intake.parse_forwarded(
        "> Op vr 11 sep 2026 om 10:35 schreef Klant <k@client.nl>:\n> Logo bijgevoegd", zone=zone
    )
    assert quoted.quoted and (quoted.from_name, quoted.from_email) == ("Klant", "k@client.nl")
    assert quoted.sent_at == datetime(2026, 9, 11, 10, 35, tzinfo=zone)
    assert quoted.body == "Logo bijgevoegd"
    # A sign-off phrase in the middle of a sentence is not a signature.
    assert (
        intake.strip_signature("Groeten aan de klant overbrengen.\nDaarna klaar.").count("\n") == 1
    )


#: An HTML-only forward as iOS Mail sends it: one line of source, ``<div>`` per line, a
#: ``<head><meta>`` in front, the original's headers in bold with ``<br>`` between them.
APPLE_MAIL_HTML = (
    '<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"></head>'
    '<body dir="auto"><div>Voor breik.nl moet ik toevoegen aan smtp2go. Dit gaat over '
    "domeinnaam.breik.nl deadline as dinsdag</div><div><br></div>"
    "<div>Met vriendelijke groet,&nbsp;</div><div>Stan Marcusse</div>"
    '<div>T. 0113 | E. stan@<a href="mailto:stan@breik.nl">breik.nl</a></div><div><br></div>'
    "<div>Begin forwarded message:</div><div><br></div>"
    "<div><b>From: </b>The EmailJS Crew &lt;support@emailjs.com&gt;<br>"
    "<b>Date: </b>21 September 2026 at 01:12:14 CEST<br><b>To: </b>stan@breik.nl<br>"
    "<b>Subject: </b><b>One of your services may have stopped working</b><br>"
    "<b>Reply-To: </b>reply@emailjs.zendesk.com</div><div><br></div>"
    "<div>EmailJS<br>The service failed three times.</div>"
    '<div>Open <a href="https://dashboard.emailjs.com/admin/events">Events</a></div>'
    "</body></html>"
)


def test_an_apple_mail_forward_is_split_in_both_readings_of_its_html() -> None:
    """The live mail (2026-09-21): iOS Mail sent HTML only, the markdown reading came back
    ``None`` (the ``<meta>`` fault, ``test_htmlmd``) and the plain reading flattened every
    ``<div>`` to a space — so the sign-off, ``Begin forwarded message:`` and the original's
    headers all sat on the instruction's own line, nothing was cut or filed, and the whole
    mail, signature and all, became the task's notes."""
    from app.core.htmlmd import html_to_markdown
    from app.core.mailbox.matching import html_to_text

    zone = ZoneInfo("Europe/Amsterdam")
    for body in (html_to_markdown(APPLE_MAIL_HTML), html_to_text(APPLE_MAIL_HTML)):
        assert body is not None
        draft = intake.parse_intake("Fwd: One of your services may have stopped working", body)
        assert draft.own_text == (
            "Voor breik.nl moet ik toevoegen aan smtp2go. Dit gaat over domeinnaam.breik.nl "
            "deadline as dinsdag"
        )
        assert draft.due_hint == "as dinsdag"
        mail = intake.parse_forwarded(draft.forwarded_text, zone=zone)
        assert (mail.from_name, mail.from_email) == ("The EmailJS Crew", "support@emailjs.com")
        assert mail.subject == "One of your services may have stopped working"
        assert mail.sent_at == datetime(2026, 9, 21, 1, 12, 14, tzinfo=zone)
        assert mail.to == [(None, "stan@breik.nl")]
        # Reply-To is a header of the block, not the first line of the forwarded body.
        assert mail.body.startswith("EmailJS") and "Reply-To" not in mail.body
    # The plain reading keeps an address that an inline tag split in the source.
    assert "stan@breik.nl" in (html_to_text(APPLE_MAIL_HTML) or "")
    # And Apple Mail's own languages for the marker.
    for marker in ("Begin doorgestuurd bericht:", "Anfang der weitergeleiteten Nachricht:"):
        own, forwarded = intake.split_forward(f"Zie onder.\n\n{marker}\n\nVan: k@x.nl")
        assert (own, forwarded.startswith(marker)) == ("Zie onder.", True)


def test_merge_links_files_the_interaction_onto_the_task() -> None:
    task_id, company_id = uuid.uuid4(), uuid.uuid4()
    outcome = IntakeOutcome(status="created", links={"task_id": task_id, "company_id": company_id})
    merged = merge_links({"company_id": None, "contact_id": uuid.uuid4()}, outcome)
    assert merged["task_id"] == task_id and merged["task_ids"] == [task_id]
    assert merged["company_id"] == company_id
    assert merge_links({"company_id": company_id}, None) == {"company_id": company_id}
    # A mail that became three tasks puts the contact moment on all three, lead first, ahead
    # of whatever the thread had already named.
    second, third, thread_task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    split = IntakeOutcome(
        status="created",
        links={"task_id": task_id, "task_ids": [task_id, second, third], "company_id": company_id},
    )
    merged = merge_links({"task_id": thread_task, "task_ids": [thread_task]}, split)
    assert merged["task_id"] == task_id
    assert merged["task_ids"] == [task_id, second, third, thread_task]


# --------------------------------------------------------------------------- #
# The feed hook, against the scripted Gmail
# --------------------------------------------------------------------------- #

INTAKE = "taak@agency.nl"


def _stub_acting_as(stub):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


async def _seed(tenant, *, history_id: str = "5", log_internal: bool = False) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                gmail_enabled=True,
                gmail_approval_mode="approval_required",
                gmail_log_internal=log_internal,
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


def receipt_links(receipts) -> list[str | None]:  # noqa: ANN001
    return [(r.hints or {}).get("interaction_id") for r in receipts]


async def _logged_email(
    org_id,  # noqa: ANN001
    *,
    owner_user_id: uuid.UUID,
    sender: str,
    to: str,
    subject: str,
    occurred_at: datetime,
    status: str = "logged",
) -> uuid.UUID:
    """A row the mailbox feed would have written for the original message."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = Interaction(
            org_id=org_id,
            kind="email",
            status=status,
            occurred_at=occurred_at,
            subject=subject,
            direction="inbound",
            owner_user_id=owner_user_id,
            owner_name="Ik",
            participants=[
                {"email": sender, "name": None, "role": "from"},
                {"email": to, "name": None, "role": "to"},
            ],
            source="gmail",
            gmail_message_id="orig-1",
            gmail_thread_id="thr-orig",
        )
        session.add(row)
        await session.commit()
        return row.id


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
        # What was forwarded is not notes: it is Sander's mail, filed on the task as a contact
        # moment under his name, with the forwarding colleague as its owner.
        assert "Ons certificaat verloopt." not in (task.description or "")
        forwarded = await _rows(t.org.id, Interaction)
        assert len(forwarded) == 1
        mail = forwarded[0]
        assert mail.source == "forwarded" and mail.status == "logged" and mail.kind == "email"
        assert mail.task_id == task.id and mail.company_id == task.company_id
        assert mail.owner_user_id == t.user.id and mail.direction == "inbound"
        assert mail.body_text == "Ons certificaat verloopt."
        assert [p["email"] for p in mail.participants] == ["sander@nova.nl"]
        assert receipt_links(await _rows(t.org.id, TaskIntakeMessage))[0] == str(mail.id)

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
        # The mail *to* the address is not a contact moment: the only timeline row is the
        # forwarded message, never the instruction that carried it.
        assert [r.source for r in await _rows(t.org.id, Interaction)] == ["forwarded"]

        # The settings screen can say when the last one arrived.
        settings = (await c.get("/api/v1/tasks/settings", headers=headers)).json()
        assert settings["intake_received_count"] == 1
        assert settings["intake_last_received_at"] is not None

        # The same message polled again (a colleague's copy, a re-offered history page) is one
        # task, decided by the database, not a second one.
        assert await _poll(t, connection_id, stub, monkeypatch) == 0
        assert len(await _rows(t.org.id, Task)) == 1


def _html_only(message: dict, html: str) -> dict:
    """The message as an HTML-only client sends it: no ``text/plain`` alternative at all."""
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "text/html",
        "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
    }
    return message


async def test_a_second_copy_spelled_the_apple_mail_way_is_one_task(
    client_for, monkeypatch
) -> None:
    """The live duplicate (2026-09-21, two tasks four minutes apart): Apple Mail writes
    ``Message-Id``, the header map was keyed on the spelling, so the receipt carried no RFC-822
    id and the fallback key — the provider's own message id — differs between the sender's Sent
    copy and the copy the address delivered. Two copies, two Gmail ids, one header: one task."""
    t = await make_tenant("intake-apple")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "breik"}, headers=headers)
        ).json()

    def _copy(message_id: str) -> dict:
        message = _html_only(
            _sent(message_id, subject="Fwd: [breik] Services stopped", body=""),
            APPLE_MAIL_HTML,
        )
        for header in message["payload"]["headers"]:
            if header["name"] == "Message-ID":
                header["name"] = "Message-Id"
                header["value"] = "<apple-1@breik.nl>"
        return message

    stub = _StubGmail(
        history=["sent-1", "delivered-1"],
        messages={"sent-1": _copy("sent-1"), "delivered-1": _copy("delivered-1")},
        history_id="9500",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    tasks = await _rows(t.org.id, Task)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.company_id == uuid.UUID(company["id"])
    receipts = await _rows(t.org.id, TaskIntakeMessage)
    assert len(receipts) == 1
    assert receipts[0].rfc822_message_id == "<apple-1@breik.nl>"
    assert receipts[0].body_markdown is not None  # the HTML reading survived its <meta>
    # No model here, so the notes are the colleague's own words — without the signature and
    # without the forwarded mail, which is a contact moment on the task under its own sender.
    assert task.description == (
        "Voor breik.nl moet ik toevoegen aan smtp2go. Dit gaat over domeinnaam.breik.nl "
        "deadline as dinsdag"
    )
    assert task.due_date is not None
    forwarded = await _rows(t.org.id, Interaction)
    assert [(m.source, m.task_id, m.subject) for m in forwarded] == [
        ("forwarded", task.id, "One of your services may have stopped working")
    ]
    assert [p["email"] for p in forwarded[0].participants][0] == "support@emailjs.com"
    assert "The service failed three times." in (forwarded[0].body_markdown or "")


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


async def test_a_mail_to_the_address_alone_is_never_a_contact_moment(
    client_for, monkeypatch
) -> None:
    """The live fault: with internal logging on, the mail *to* ``taak@`` landed in the sender's
    review queue as a pending contact moment filed on the task it had just made."""
    t = await make_tenant("intake-only")
    connection_id = await _seed(t, log_internal=True)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
    message = _sent("msg-o", subject="[Nova Fietsen] DNS nakijken", body="Even de MX checken.")
    stub = _StubGmail(history=["msg-o"], messages={"msg-o": message}, history_id="9500")
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    assert len(await _rows(t.org.id, Task)) == 1
    assert await _rows(t.org.id, Interaction) == []


async def test_a_forwarded_mail_the_mailbox_already_logged_is_filed_not_copied(
    client_for, monkeypatch
) -> None:
    """The client's mail arrived in my connected mailbox and was logged; forwarding it to the
    task address files *that* row onto the task rather than putting the e-mail on the timeline
    a second time."""
    t = await make_tenant("intake-adopt")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    zone = ZoneInfo("Europe/Amsterdam")
    async with client_for(t.host) as c:
        await _configure(c, headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
    original = await _logged_email(
        t.org.id,
        owner_user_id=t.user.id,
        sender="sander@nova.nl",
        to="me@agency.nl",
        subject="SSL verlengen",
        occurred_at=datetime(2026, 9, 11, 10, 35, tzinfo=zone),
    )
    message = _sent(
        "msg-f",
        subject="Fwd: [Nova Fietsen] SSL verlengen",
        body=(
            "Oppakken graag.\n\n---------- Forwarded message ---------\n"
            "From: Sander <sander@nova.nl>\nDate: Fri, 11 Sept 2026 at 10:35\n"
            "Subject: SSL verlengen\nTo: Ik <me@agency.nl>\n\nOns certificaat verloopt."
        ),
    )
    stub = _StubGmail(history=["msg-f"], messages={"msg-f": message}, history_id="9600")
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    task = (await _rows(t.org.id, Task))[0]
    assert task.company_id == uuid.UUID(company["id"])
    rows = await _rows(t.org.id, Interaction)
    assert [r.id for r in rows] == [original]
    assert rows[0].task_id == task.id and rows[0].company_id == task.company_id
    links = await _rows(t.org.id, InteractionTask)
    assert [(link.interaction_id, link.task_id) for link in links] == [(original, task.id)]
    assert receipt_links(await _rows(t.org.id, TaskIntakeMessage)) == [str(original)]
    assert "Ons certificaat verloopt." not in (task.description or "")


async def test_an_attachment_the_client_did_not_type_is_typed_by_its_name(
    client_for, monkeypatch, tmp_path
) -> None:
    """The live fault: a ``.md`` spec arrived as ``application/octet-stream`` and was dropped
    without a word, on a mail that said "voeg de bijlage toe"."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("intake-md")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
    message = _sent("msg-m", subject="[Nova Fietsen] Specs", body="Zie bijlage.")
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": message["payload"]["body"]["data"]}},
            {
                "filename": "specificaties.md",
                "mimeType": "application/octet-stream",
                "body": {"attachmentId": "att-md", "size": 5},
            },
            {
                "filename": "build.exe",
                "mimeType": "application/octet-stream",
                "body": {"attachmentId": "att-exe", "size": 5},
            },
        ],
    }
    stub = _StubGmail(history=["msg-m"], messages={"msg-m": message}, history_id="9700")
    stub.messages["att-md"] = {"data": base64.urlsafe_b64encode(b"# Spec").decode()}
    stub.messages["att-exe"] = {"data": base64.urlsafe_b64encode(b"MZ...").decode()}
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    task = (await _rows(t.org.id, Task))[0]
    stored = await _rows(t.org.id, StoredFile)
    assert [(f.filename, f.content_type, f.entity_id) for f in stored] == [
        ("specificaties.md", "text/markdown", task.id)
    ]
    # The one it still refused is named on the receipt and in the task's own notes, in the
    # org's language — never silently dropped.
    receipt = (await _rows(t.org.id, TaskIntakeMessage))[0]
    assert receipt.hints["skipped_attachments"] == ["build.exe"]
    description = task.description or ""
    assert "Zie bijlage." in description
    assert "De bijlage “build.exe” uit de e-mail kon niet worden bewaard" in description


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
                name="submit_intake_plan",
                input={
                    "tasks": [
                        {
                            "title": "Nieuwsbrief opzetten",  # one task: the subject wins
                            "company_id": company["id"],
                            "assignee_user_id": str(uuid.uuid4()),  # not on the shortlist
                            "due_date": "2027-01-15",  # the sender said nothing: filled
                            "summary": "Nieuwsbrief-template bouwen in Mailchimp.",
                            "checklist_items": [{"title": "Template"}, {"title": "Testmail"}],
                            "links": [
                                {"url": "https://nova.nl/huisstijl"},
                                {"url": "https://invented.example"},  # not in the mail
                            ],
                        }
                    ]
                },
            )
        ]

    async def _flush(self, feature):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.modules.tasks.intake_ai.available", _available)
    monkeypatch.setattr("app.core.ai.service.AIService.complete", _complete)
    monkeypatch.setattr("app.core.ai.service.AIService.flush_usage", _flush)
    monkeypatch.setattr("app.core.ai.service.AIService.truncated", property(lambda self: False))

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
    assert task.title == "Nieuwsbrief voor Nova"
    assert task.company_id == uuid.UUID(company["id"])
    assert task.assignee_user_id == t.user.id  # the invented id was dropped; the sender holds it
    assert task.due_date == date(2027, 1, 15)
    # The notes are the model's summary and nothing else: the colleague's words are what it
    # was written from, and pasting them underneath was the mail in the task twice.
    assert task.description == "Nieuwsbrief-template bouwen in Mailchimp."
    receipts = await _rows(t.org.id, TaskIntakeMessage)
    assert receipts[0].hints["by_model"] == ["company", "due_date"]
    async with client_for(t.host) as c:
        detail = (await c.get(f"/api/v1/tasks/{task.id}", headers=headers)).json()
        assert [i["title"] for i in detail["checklists"][0]["items"]] == ["Template", "Testmail"]
        assert [link["url"] for link in detail["links"]] == ["https://nova.nl/huisstijl"]


def _model(monkeypatch, tasks: list[dict], *, captured: dict | None = None) -> None:
    """The model half scripted: ``task_intake`` on, one call answering ``tasks``."""

    async def _available(ctx):  # noqa: ANN001
        return True

    async def _complete(  # noqa: ANN001
        self, feature, *, system, messages, tools, force_tool=None, **kw
    ):
        from app.core.ai.providers import ToolCall

        if captured is not None:
            captured["system"] = system
            captured["document"] = messages[0].content
        return "", [ToolCall(id="c1", name="submit_intake_plan", input={"tasks": tasks})]

    async def _flush(self, feature):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.modules.tasks.intake_ai.available", _available)
    monkeypatch.setattr("app.core.ai.service.AIService.complete", _complete)
    monkeypatch.setattr("app.core.ai.service.AIService.flush_usage", _flush)
    monkeypatch.setattr("app.core.ai.service.AIService.truncated", property(lambda self: False))


async def _colleague(t, email: str, name: str) -> uuid.UUID:  # noqa: ANN001
    """An active colleague the model may name — on the shortlist and on the roster."""
    from pwdlib import PasswordHash

    from app.core.auth.models import User
    from tests.conftest import add_membership

    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=name,
            hashed_password=PasswordHash.recommended().hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, user.id)
        await session.commit()
        return user.id


async def test_one_mail_becomes_the_tasks_the_model_reads_in_it(
    client_for, monkeypatch, tmp_path
) -> None:
    """A colleague empties a phone call into one mail: two clients, two colleagues, two
    deadlines, two attachments. Every rule holds per task, the forwarded mail is filed once on
    both, each file follows the task that claimed it, and the sender hears about it once."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("intake-split")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    lotte = await _colleague(t, "lotte@agency.nl", "Lotte de Vries")
    async with client_for(t.host) as c:
        await _configure(c, headers)
        nova = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
        klok = (await c.post("/api/v1/companies", json={"name": "Klokuus"}, headers=headers)).json()
    captured: dict = {}
    _model(
        monkeypatch,
        [
            {
                "title": "DNS voor Nova omzetten",
                "summary": "DNS van nova.nl naar Cloudflare.",
                "company_id": nova["id"],
                "due_date": "2027-02-01",
                "checklist_items": [{"title": "Records exporteren"}, {"title": "NS wijzigen"}],
                "attachments": ["dns.txt"],
            },
            {
                "title": "Nieuwsbrief voor Klokuus",
                "summary": "Template in Mailchimp.",
                "company_id": klok["id"],
                "assignee_user_id": str(lotte),
                "due_date": "2027-02-10",
                "attachments": ["huisstijl.pdf", "verzonnen.pdf"],  # the second is not in the mail
            },
        ],
        captured=captured,
    )
    message = _sent(
        "msg-split",
        subject="Fwd: Twee dingen na het belletje",
        body=(
            "Sander van Nova wil de DNS omgezet hebben en Lotte moet de nieuwsbrief voor "
            "Klokuus doen.\n\n"
            "---------- Forwarded message ---------\nFrom: Sander <sander@nova.nl>\n\n"
            "Kunnen jullie de DNS omzetten?"
        ),
    )
    message["payload"] = {
        "headers": message["payload"]["headers"],
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": message["payload"]["body"]["data"]}},
            {
                "filename": "dns.txt",
                "mimeType": "text/plain",
                "body": {"attachmentId": "a-dns", "size": 3},
            },
            {
                "filename": "huisstijl.pdf",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "a-pdf", "size": 9},
            },
            {
                "filename": "los.png",
                "mimeType": "image/png",
                "body": {"attachmentId": "a-png", "size": 8},
            },
        ],
    }
    stub = _StubGmail(history=["msg-split"], messages={"msg-split": message}, history_id="9800")
    stub.messages["a-dns"] = {"data": base64.urlsafe_b64encode(b"ns1").decode()}
    stub.messages["a-pdf"] = {"data": base64.urlsafe_b64encode(b"%PDF-fake").decode()}
    stub.messages["a-png"] = {"data": base64.urlsafe_b64encode(b"\x89PNG\r\n\x1a\n").decode()}
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    # The prompt states the splitting rule, and the document names the files a task may claim.
    assert "Submit several ONLY when" in captured["system"]
    assert "huisstijl.pdf" in captured["document"]

    tasks = sorted(await _rows(t.org.id, Task), key=lambda x: x.created_at)
    assert [x.title for x in tasks] == ["DNS voor Nova omzetten", "Nieuwsbrief voor Klokuus"]
    dns, news = tasks
    assert dns.company_id == uuid.UUID(nova["id"]) and news.company_id == uuid.UUID(klok["id"])
    assert dns.assignee_user_id == t.user.id and news.assignee_user_id == lotte
    assert (dns.due_date, news.due_date) == (date(2027, 2, 1), date(2027, 2, 10))
    assert dns.description == "DNS van nova.nl naar Cloudflare."
    assert news.description == "Template in Mailchimp."
    async with client_for(t.host) as c:
        detail = (await c.get(f"/api/v1/tasks/{dns.id}", headers=headers)).json()
        assert [i["title"] for i in detail["checklists"][0]["items"]] == [
            "Records exporteren",
            "NS wijzigen",
        ]
    # One forwarded mail, one contact moment, on both rosters — the lead is chip 0.
    mails = await _rows(t.org.id, Interaction)
    assert len(mails) == 1 and mails[0].task_id == dns.id
    roster = sorted(await _rows(t.org.id, InteractionTask), key=lambda r: r.position)
    assert [r.task_id for r in roster] == [dns.id, news.id]
    # Each file with the task that claimed it; the one nobody claimed with the lead.
    stored = await _rows(t.org.id, StoredFile)
    assert sorted((f.filename, f.entity_id) for f in stored) == sorted(
        [("dns.txt", dns.id), ("huisstijl.pdf", news.id), ("los.png", dns.id)]
    )
    # The receipt names every task; the sender heard once, in the split's own sentence.
    receipt = (await _rows(t.org.id, TaskIntakeMessage))[0]
    assert receipt.status == "created" and receipt.task_id == dns.id
    assert receipt.hints["task_ids"] == [str(dns.id), str(news.id)]
    assert [d["title"] for d in receipt.hints["tasks"]] == [dns.title, news.title]
    assert receipt.hints["tasks"][1]["by_model"] == ["company", "assignee", "due_date"]
    events = await _rows(t.org.id, NotificationEvent)
    intake_events = [e for e in events if e.event_type.startswith("task.intake")]
    assert [e.event_type for e in intake_events] == ["task.intake_split"]
    assert intake_events[0].payload["count"] == 2 and intake_events[0].entity_id == dns.id
    assert "Nieuwsbrief voor Klokuus" in intake_events[0].payload["titles"]
    # Lotte was handed a task by the sender's mail and hears that as usual; the sender is
    # excluded from "assigned" for their own mail and hears the split's sentence instead.
    inbox = await _rows(t.org.id, Notification)
    by_event = {e.id: e for e in events}
    assert sorted(by_event[n.event_id].event_type for n in inbox if n.user_id == t.user.id) == [
        "task.intake_split"
    ]
    assert [by_event[n.event_id].event_type for n in inbox if n.user_id == lotte] == [
        "task.assigned"
    ]
    trail = [a.action for a in await _rows(t.org.id, TaskActivity) if a.task_id == news.id]
    assert "ai_intake_split" in trail
    async with client_for(t.host) as c:
        listed = (await c.get("/api/v1/tasks/intake", headers=headers)).json()
        assert listed[0]["task_ids"] == [str(dns.id), str(news.id)]


async def test_the_senders_own_words_hold_for_every_task_of_a_split(
    client_for, monkeypatch
) -> None:
    """``klant:`` and ``deadline:`` are statements about the mail: the model may split it but
    not move a task to another client or another day."""
    t = await make_tenant("intake-split-words")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers)
        nova = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
        other = (
            await c.post("/api/v1/companies", json={"name": "Andere Klant"}, headers=headers)
        ).json()
    _model(
        monkeypatch,
        [
            {"title": "Banner", "company_id": other["id"], "due_date": "2027-03-03"},
            {"title": "Landingspagina", "company_id": other["id"], "due_date": "2027-03-04"},
        ],
    )
    message = _sent(
        "msg-words",
        subject="Campagne",
        body="klant: Nova Fietsen\ndeadline: 2027-01-20\n\nBanner en landingspagina maken.",
    )
    stub = _StubGmail(history=["msg-words"], messages={"msg-words": message}, history_id="9810")
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    tasks = await _rows(t.org.id, Task)
    assert sorted(x.title for x in tasks) == ["Banner", "Landingspagina"]
    assert {x.company_id for x in tasks} == {uuid.UUID(nova["id"])}
    assert {x.due_date for x in tasks} == {date(2027, 1, 20)}
    receipt = (await _rows(t.org.id, TaskIntakeMessage))[0]
    assert all(d["by_model"] == [] for d in receipt.hints["tasks"])


async def test_a_split_with_a_task_that_has_no_client_parks_whole_and_finishes_whole(
    client_for, monkeypatch
) -> None:
    """A mail is one act: one task without a client parks the whole mail, the card carries the
    split, and finishing it makes every task — or, by choice, one."""
    t = await make_tenant("intake-split-park")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _configure(c, headers, intake_default_due_days=2)
        nova = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
        later = (
            await c.post("/api/v1/companies", json={"name": "Later Gekozen"}, headers=headers)
        ).json()
    plan = [
        {"title": "Offerte nasturen", "company_id": nova["id"], "summary": "Offerte v2 mailen."},
        {
            "title": "Intake nieuwe klant",
            "checklist_items": [{"title": "Bellen"}, {"title": "Voorstel"}],
        },
    ]
    _model(monkeypatch, plan)
    message = _sent(
        "msg-sp",
        subject="Na de meeting",
        body="Offerte voor Nova Fietsen nasturen, en de intake van de nieuwe klant plannen.",
    )
    stub = _StubGmail(history=["msg-sp"], messages={"msg-sp": message}, history_id="9820")
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    assert await _rows(t.org.id, Task) == []
    row = (await _rows(t.org.id, TaskIntakeMessage))[0]
    assert row.status == "needs_client" and row.reason == "no_client"
    assert [d["title"] for d in row.hints["tasks"]] == ["Offerte nasturen", "Intake nieuwe klant"]
    assert row.hints["tasks"][0]["company_name"] == "Nova Fietsen"
    assert row.hints["tasks"][1]["company_id"] is None

    async with client_for(t.host) as c:
        # Finished whole: the picked client fills only the task that had none.
        res = await c.post(
            f"/api/v1/tasks/intake/{row.id}/create",
            json={"company_id": later["id"]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        lead = res.json()
        tasks = {x.title: x for x in await _rows(t.org.id, Task)}
        assert set(tasks) == {"Offerte nasturen", "Intake nieuwe klant"}
        assert lead["id"] == str(tasks["Offerte nasturen"].id)
        assert tasks["Offerte nasturen"].company_id == uuid.UUID(nova["id"])
        assert tasks["Intake nieuwe klant"].company_id == uuid.UUID(later["id"])
        assert tasks["Intake nieuwe klant"].due_date == org_today() + timedelta(days=2)
        detail = (
            await c.get(f"/api/v1/tasks/{tasks['Intake nieuwe klant'].id}", headers=headers)
        ).json()
        assert [i["title"] for i in detail["checklists"][0]["items"]] == ["Bellen", "Voorstel"]
        listed = (await c.get("/api/v1/tasks/intake", headers=headers)).json()
        assert len(listed[0]["task_ids"]) == 2 and listed[0]["task_id"] == lead["id"]

    # The same plan, folded by choice: one task, each planned task a step.
    t2 = await make_tenant("intake-split-fold")
    connection_id = await _seed(t2)
    headers = await auth_cookie(t2.user)
    async with client_for(t2.host) as c:
        await _configure(c, headers)
        nova2 = (
            await c.post("/api/v1/companies", json={"name": "Nova Fietsen"}, headers=headers)
        ).json()
    _model(monkeypatch, [{**plan[0], "company_id": nova2["id"]}, plan[1]])
    stub = _StubGmail(history=["msg-sp"], messages={"msg-sp": message}, history_id="9830")
    assert await _poll(t2, connection_id, stub, monkeypatch) == 0
    row = (await _rows(t2.org.id, TaskIntakeMessage))[0]
    async with client_for(t2.host) as c:
        res = await c.post(
            f"/api/v1/tasks/intake/{row.id}/create",
            json={"company_id": nova2["id"], "as_one": True},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert len(await _rows(t2.org.id, Task)) == 1
        task = res.json()
        assert task["title"] == "Na de meeting"
        assert (
            "**Offerte nasturen**" in task["description"]
            and "Offerte v2 mailen." in task["description"]
        )
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        items = detail["checklists"][0]["items"]
        assert [i["title"] for i in items] == ["Offerte nasturen", "Intake nieuwe klant"]
        assert "- Bellen" in (items[1]["description"] or "")


def test_a_plan_is_grounded_per_task_and_an_empty_one_is_no_plan() -> None:
    from app.modules.tasks.intake_ai import MAX_TASKS, plan_from_call

    class _Candidates:
        def __init__(self, ids, members, labels):  # noqa: ANN001
            self._ids, self._members, self._labels = ids, members, labels

        def ids(self):  # noqa: ANN202
            return self._ids

        def member_ids(self):  # noqa: ANN202
            return self._members

        def label_ids(self):  # noqa: ANN202
            return self._labels

    company, member = uuid.uuid4(), uuid.uuid4()
    candidates = _Candidates({str(company)}, {str(member)}, set())
    today = date(2026, 9, 25)
    plan = plan_from_call(
        {
            "tasks": [
                {"title": "A", "company_id": str(company), "attachments": ["a.pdf", "nope.pdf"]},
                {"title": "", "summary": "no title in a split: dropped"},
                {"title": "B", "assignee_user_id": str(company)},  # a client id is not a member
                "not a dict",
            ]
        },
        candidates=candidates,
        body="",
        today=today,
        attachment_names={"a.pdf"},
    )
    assert plan is not None and plan.split
    assert [x.title for x in plan.tasks] == ["A", "B"]
    assert plan.tasks[0].company_id == company and plan.tasks[0].attachments == ["a.pdf"]
    assert plan.tasks[1].assignee_user_id is None
    # One untitled task is still a plan (the subject names it); nothing at all is none.
    alone = plan_from_call(
        {"tasks": [{"title": "", "summary": "s"}]}, candidates=candidates, body="", today=today
    )
    assert alone is not None and not alone.split and alone.tasks[0].summary == "s"
    assert plan_from_call({"tasks": []}, candidates=candidates, body="", today=today) is None
    assert (
        plan_from_call({"title": "old shape"}, candidates=candidates, body="", today=today) is None
    )
    many = plan_from_call(
        {"tasks": [{"title": f"T{i}"} for i in range(MAX_TASKS + 3)]},
        candidates=candidates,
        body="",
        today=today,
    )
    assert many is not None and len(many.tasks) == MAX_TASKS


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
        res = await c.put("/api/v1/tasks/settings", json={"intake_address": None}, headers=headers)
        assert res.json()["intake_address"] is None and res.json()["intake_default_due_days"] == 2
