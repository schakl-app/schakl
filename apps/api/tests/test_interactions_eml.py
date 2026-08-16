"""Uploading a `.eml` as an email contactmoment (#262).

The rules under test, in the order they matter:

- the parsed row lands in the **same field shape** a gmail-synced email has, so both render
  identically — subject, participants, occurred_at, body, ``rfc822_message_id``;
- ``email`` stays the protected kind on the ordinary create endpoint: only this path writes one;
- an already-logged ``Message-ID`` **warns** (409) and logs on a second, deliberate try;
- attachments land in the shared file store, and one the guardrails refuse is *counted*,
  never silently dropped;
- an uploaded row edits and deletes like any manual row (no mailbox, so no review flow),
  but never re-types itself into a note;
- and none of it crosses a tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from email.message import EmailMessage

from app.config import settings
from app.modules.interactions.eml import EmlParseError, looks_like_eml, parse_eml
from tests.conftest import auth_cookie, make_tenant

_PDF = b"%PDF-1.4 " + b"0" * 32


def build_eml(
    *,
    subject: str = "Offerte akkoord",
    sender: str = "Klant Naam <Klant@Client.NL>",
    to: str = "team@agency.nl",
    cc: str | None = "boss@client.nl",
    date: str | None = "Fri, 10 Jul 2026 14:30:00 +0200",
    message_id: str | None = "<abc123@mail.client.nl>",
    in_reply_to: str | None = None,
    references: str | None = None,
    body: str = "Bij deze akkoord op de offerte.\n\nGroet,\nKlant",
    attachments: tuple[tuple[str, str, str, bytes], ...] = (),
    html: bool = False,
) -> bytes:
    """One exported message, the way a mail client writes it."""
    message = EmailMessage()
    if subject:
        message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if date:
        message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body, subtype="html" if html else "plain")
    for filename, maintype, subtype, data in attachments:
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def _upload(name: str = "bericht.eml", content_type: str = "application/octet-stream", **kwargs):
    """Multipart payload for the endpoint. The content type is deliberately the vague one
    Outlook/Windows sends — the extension is what must carry the day."""
    return {"file": (name, build_eml(**kwargs), content_type)}


# --------------------------------------------------------------------------- #
# The parser itself (pure — no tenant, no HTTP)
# --------------------------------------------------------------------------- #
def test_parser_reads_headers_body_and_attachments() -> None:
    parsed = parse_eml(build_eml(attachments=(("offerte.pdf", "application", "pdf", _PDF),)))
    assert parsed.subject == "Offerte akkoord"
    assert parsed.occurred_at == datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
    assert parsed.rfc822_message_id == "<abc123@mail.client.nl>"
    assert parsed.from_email == "klant@client.nl"
    # Addresses lowercased, display names kept, roles in From/To/Cc order — the gmail shape.
    assert parsed.participants == [
        {"email": "klant@client.nl", "name": "Klant Naam", "role": "from"},
        {"email": "team@agency.nl", "name": None, "role": "to"},
        {"email": "boss@client.nl", "name": None, "role": "cc"},
    ]
    assert parsed.body_text is not None and parsed.body_text.startswith("Bij deze akkoord")
    assert parsed.snippet == "Bij deze akkoord op de offerte. Groet, Klant"
    assert [(a.filename, a.content_type) for a in parsed.attachments] == [
        ("offerte.pdf", "application/pdf")
    ]


def test_parser_falls_back_to_html_and_tolerates_a_missing_date() -> None:
    parsed = parse_eml(
        build_eml(
            html=True,
            body="<html><body><p>Hallo &amp; tot ziens</p><script>evil()</script></body></html>",
            date=None,
            message_id=None,
        )
    )
    # Script/style dropped, entities unescaped — the same stripping the gmail body gets.
    assert parsed.body_text == "Hallo & tot ziens"
    # No usable Date header: the parser says so rather than inventing an instant.
    assert parsed.occurred_at is None
    assert parsed.rfc822_message_id is None


def test_parser_extracts_threading_headers() -> None:
    """#272: References (oldest→newest) then In-Reply-To become reference_ids, deduped, so [0]
    is the thread root that folds an uploaded reply onto its conversation."""
    parsed = parse_eml(
        build_eml(
            message_id="<c3@mail>", references="<a1@mail> <b2@mail>", in_reply_to="<b2@mail>"
        )
    )
    # In-Reply-To duplicates the newest References entry — dropped; order preserved, root first.
    assert parsed.reference_ids == ["<a1@mail>", "<b2@mail>"]
    # A standalone email carries no chain.
    assert parse_eml(build_eml(message_id="<solo@mail>")).reference_ids == []


def test_parser_refuses_bytes_that_are_not_a_message() -> None:
    # A renamed archive parses "successfully" into a headerless message — which would land on
    # the timeline as an empty contactmoment. It must be refused instead.
    try:
        parse_eml(b"PK\x03\x04\x00\x00 not an email at all")
    except EmlParseError:
        pass
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("headerless bytes were accepted as an email")


def test_eml_is_recognised_by_extension_or_content_type() -> None:
    # Browsers and operating systems label exported mail inconsistently; either signal is enough.
    assert looks_like_eml("Bericht.EML", "application/octet-stream")
    assert looks_like_eml("blob", "message/rfc822; charset=utf-8")
    assert not looks_like_eml("archief.zip", "application/zip")


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #
async def test_upload_lands_in_the_gmail_field_shape_with_its_attachment(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("eml-shape")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Website", "company_id": company["id"]},
                headers=headers,
            )
        ).json()

        created = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(attachments=(("offerte.pdf", "application", "pdf", _PDF),)),
            data={"project_id": project["id"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        row = payload["interaction"]
        assert row["kind"] == "email"
        assert row["source"] == "upload"
        assert row["status"] == "logged"  # a deliberate upload needs no review step
        assert row["subject"] == "Offerte akkoord"
        assert row["occurred_at"].startswith("2026-07-10T12:30")
        assert row["direction"] == "inbound"  # the sender is not one of us
        assert row["snippet"].startswith("Bij deze akkoord")
        assert row["body_text"].startswith("Bij deze akkoord")
        assert [(p["email"], p["role"]) for p in row["participants"]] == [
            ("klant@client.nl", "from"),
            ("team@agency.nl", "to"),
            ("boss@client.nl", "cc"),
        ]
        # A project link derives the client, exactly like a manual row (#22).
        assert row["project_id"] == project["id"]
        assert row["company_id"] == company["id"]
        assert payload["attachments_stored"] == 1
        assert payload["attachments_skipped"] == 0

        # The attachment is a normal stored file on the interaction (#123/#180) — the same
        # listing the detail modal reads, and it downloads.
        files = (
            await c.get(
                "/api/v1/files",
                params={"entity_type": "interaction", "entity_id": row["id"]},
                headers=headers,
            )
        ).json()
        assert [f["filename"] for f in files] == ["offerte.pdf"]
        served = await c.get(f"/api/v1/files/{files[0]['id']}", headers=headers)
        assert served.status_code == 200
        assert served.content == _PDF

        # And the create is on the row's own activity trail (§16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "interaction", "entity_id": row["id"]},
                headers=headers,
            )
        ).json()
        assert any(entry["action"] == "created" for entry in trail)


async def test_upload_from_a_colleague_is_outbound(client_for) -> None:
    t = await make_tenant("eml-outbound")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(sender=f"Wij <{t.user.email}>", to="klant@client.nl"),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["interaction"]["direction"] == "outbound"


async def test_upload_refuses_a_non_eml_and_an_unreadable_one(client_for) -> None:
    t = await make_tenant("eml-refuse")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        wrong_type = await c.post(
            "/api/v1/interactions/upload-eml",
            files={"file": ("archief.zip", b"PK\x03\x04", "application/zip")},
            headers=headers,
        )
        assert wrong_type.status_code == 422
        assert wrong_type.json()["error"]["fields"]["file"] == "errors.interactions_eml_type"

        # Right extension, wrong content: refused before anything reaches the timeline.
        unreadable = await c.post(
            "/api/v1/interactions/upload-eml",
            files={"file": ("bericht.eml", b"\x00\x01\x02 niet een mail", "message/rfc822")},
            headers=headers,
        )
        assert unreadable.status_code == 422
        assert unreadable.json()["error"]["fields"]["file"] == "errors.interactions_eml_invalid"

        assert (await c.get("/api/v1/interactions", headers=headers)).json()["total"] == 0


async def test_duplicate_message_id_warns_but_still_allows(client_for) -> None:
    t = await make_tenant("eml-dupe")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = await c.post(
            "/api/v1/interactions/upload-eml", files=_upload(), headers=headers
        )
        assert first.status_code == 201, first.text

        # The same message again: a question, not a wall.
        again = await c.post(
            "/api/v1/interactions/upload-eml", files=_upload(), headers=headers
        )
        assert again.status_code == 409
        assert again.json()["error"]["message"] == "errors.interactions_eml_duplicate"

        confirmed = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(),
            data={"allow_duplicate": "true"},
            headers=headers,
        )
        assert confirmed.status_code == 201, confirmed.text
        assert (await c.get("/api/v1/interactions", headers=headers)).json()["total"] == 2


async def test_a_refused_attachment_is_counted_not_dropped_silently(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("eml-attach")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(
                attachments=(
                    ("offerte.pdf", "application", "pdf", _PDF),
                    ("macro.exe", "application", "x-msdownload", b"MZ" + b"0" * 8),
                )
            ),
            headers=headers,
        )
        # The disallowed type must not lose the message — but the caller is told.
        assert created.status_code == 201, created.text
        assert created.json()["attachments_stored"] == 1
        assert created.json()["attachments_skipped"] == 1


async def test_manual_create_still_refuses_the_email_kind(client_for) -> None:
    """The guard the upload path deliberately does not loosen (#174)."""
    t = await make_tenant("eml-guard")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "email",
                "occurred_at": datetime(2026, 7, 10, 14, 30, tzinfo=UTC).isoformat(),
                "subject": "Handmatige e-mail",
            },
            headers=headers,
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["fields"]["kind"] == "errors.interactions_kind_not_manual"


async def test_uploaded_row_edits_and_deletes_but_never_re_types_itself(client_for) -> None:
    t = await make_tenant("eml-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        row = (
            await c.post("/api/v1/interactions/upload-eml", files=_upload(), headers=headers)
        ).json()["interaction"]

        # No mailbox behind it, so no review flow owns it: an ordinary edit re-links it and
        # leaves the received message alone (an unsent field is untouched).
        moved = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["company_id"] == company["id"]
        assert moved.json()["body_text"] == row["body_text"]

        # …but an email may not launder itself into a note.
        retyped = await c.patch(
            f"/api/v1/interactions/{row['id']}", json={"kind": "note"}, headers=headers
        )
        assert retyped.status_code == 422
        assert retyped.json()["error"]["fields"]["kind"] == "errors.interactions_kind_not_manual"

        # The review actions stay gmail-only.
        assert (
            await c.post(f"/api/v1/interactions/{row['id']}/approve", headers=headers)
        ).status_code == 409

        assert (
            await c.delete(f"/api/v1/interactions/{row['id']}", headers=headers)
        ).status_code == 204


async def test_upload_tenant_isolation(client_for) -> None:
    a = await make_tenant("eml-iso-a")
    b = await make_tenant("eml-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        b_company = (
            await cb.post("/api/v1/companies", json={"name": "Andere klant"}, headers=b_headers)
        ).json()
    async with client_for(a.host) as ca:
        # Another tenant's company id is not a link target — it reads as absent, not forbidden.
        cross = await ca.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(),
            data={"company_id": b_company["id"]},
            headers=a_headers,
        )
        assert cross.status_code == 422
        assert cross.json()["error"]["fields"]["company_id"] == "errors.not_found"

        row = (
            await ca.post("/api/v1/interactions/upload-eml", files=_upload(), headers=a_headers)
        ).json()["interaction"]
    async with client_for(b.host) as cb:
        assert (
            await cb.get(f"/api/v1/interactions/{row['id']}", headers=b_headers)
        ).status_code == 404
        assert (await cb.get("/api/v1/interactions", headers=b_headers)).json()["total"] == 0
        # The same Message-ID in another tenant is a fresh message, never a duplicate.
        mine = await cb.post(
            "/api/v1/interactions/upload-eml", files=_upload(), headers=b_headers
        )
        assert mine.status_code == 201, mine.text
        assert uuid.UUID(mine.json()["interaction"]["id"]) != uuid.UUID(row["id"])


# --------------------------------------------------------------------------- #
# The formatted body: an HTML mail keeps its shape, and its inline images are
# content of that body rather than attachments of the message.
# --------------------------------------------------------------------------- #
_GIF = b"GIF89a" + b"\x00" * 26


def build_rich_eml(*, logo: bytes = _GIF, cid: str = "logo@bureau") -> bytes:
    """A multipart/related HTML mail with a signature logo — the everyday shape."""
    message = EmailMessage()
    message["Subject"] = "Voorstel hosting"
    message["From"] = "Klant Naam <klant@client.nl>"
    message["To"] = "team@agency.nl"
    message["Date"] = "Fri, 10 Jul 2026 14:30:00 +0200"
    message["Message-ID"] = "<rich-1@mail.client.nl>"
    message.set_content("Beste Stan, zie de opsomming.")
    message.add_alternative(
        "<html><body><p>Beste Stan,</p>"
        "<p>Hierbij het <strong>voorstel</strong>:</p>"
        "<ul><li>Hosting 2026</li><li>SSL-certificaat</li></ul>"
        '<p>Zie <a href="https://client.nl/offerte">de offerte</a>.</p>'
        f'<p><img src="cid:{cid}" alt="Bureau"></p>'
        "</body></html>",
        subtype="html",
    )
    html_part = message.get_payload()[1]
    html_part.add_related(
        logo, maintype="image", subtype="gif", cid=f"<{cid}>", filename="image001.gif"
    )
    return message.as_bytes()


def test_parser_keeps_the_formatting_and_marks_the_inline_image() -> None:
    parsed = parse_eml(build_rich_eml())
    # The plain-text half is untouched: it is what search reads and the snippet is cut from.
    assert parsed.body_text is not None and "Beste Stan" in parsed.body_text
    assert parsed.body_markdown == (
        "Beste Stan,\n"
        "\n"
        "Hierbij het **voorstel**:\n"
        "\n"
        "- Hosting 2026\n"
        "- SSL-certificaat\n"
        "\n"
        "Zie [de offerte](https://client.nl/offerte).\n"
        "\n"
        "![Bureau](cid:logo@bureau)"
    )
    # The logo is part of the body, not an attachment of the message.
    assert [(a.filename, a.content_id) for a in parsed.attachments] == [
        ("image001.gif", "logo@bureau")
    ]


def test_a_plain_text_mail_gets_no_markdown() -> None:
    """Received text is not our markdown: a sender's asterisks must stay asterisks."""
    parsed = parse_eml(build_eml(body="Dit is *belangrijk* en [zie bijlage]"))
    assert parsed.body_markdown is None
    assert parsed.body_text == "Dit is *belangrijk* en [zie bijlage]"


async def test_upload_renders_formatted_and_hides_the_signature_logo(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("eml-rich")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/interactions/upload-eml",
            files={"file": ("bericht.eml", build_rich_eml(), "message/rfc822")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        # A logo is not a "bijlage": reporting one would describe a message nobody received.
        assert payload["attachments_stored"] == 0
        assert payload["attachments_skipped"] == 0
        row = payload["interaction"]

        # The stored body points at the file the logo became — a marker, never a URL.
        assert row["body_markdown"] is not None
        assert "**voorstel**" in row["body_markdown"]
        assert "](file:" in row["body_markdown"] and "cid:" not in row["body_markdown"]

        # …and that file is not in the attachment list the detail view draws.
        listed = await c.get(
            f"/api/v1/files?entity_type=interaction&entity_id={row['id']}", headers=headers
        )
        assert listed.json() == []
        inline = await c.get(
            f"/api/v1/files?entity_type=interaction&entity_id={row['id']}&include_inline=true",
            headers=headers,
        )
        assert [f["content_id"] for f in inline.json()] == ["logo@bureau"]

        # The marker resolves to a file that actually serves.
        file_id = row["body_markdown"].split("](file:")[1].split(")")[0]
        served = await c.get(f"/api/v1/files/{file_id}", headers=headers)
        assert served.status_code == 200 and served.content == _GIF


async def test_the_same_signature_logo_costs_one_object(client_for, tmp_path, monkeypatch) -> None:
    """Why the two halves ship together: keeping the formatting means keeping the images, and
    a sender's logo arrives on every message they send (docs/STORAGE.md)."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    from sqlalchemy import func, select

    from app.core.storage.models import FileBlob
    from app.db import async_session_maker, set_current_org

    t = await make_tenant("eml-logo-dedup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for index in range(3):
            body = build_rich_eml().replace(
                b"<rich-1@mail.client.nl>", f"<rich-{index}@mail.client.nl>".encode()
            )
            created = await c.post(
                "/api/v1/interactions/upload-eml",
                files={"file": (f"m{index}.eml", body, "message/rfc822")},
                headers=headers,
            )
            assert created.status_code == 201, created.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        blobs = await session.scalar(
            select(func.count()).select_from(FileBlob).where(FileBlob.org_id == t.org.id)
        )
    assert blobs == 1, "three messages, one logo, one object"


async def test_editing_the_body_drops_the_converted_formatting(
    client_for, tmp_path, monkeypatch
) -> None:
    """An edited body is the author's, not the sender's: leaving the conversion behind would
    render one text and store another."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("eml-rich-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        row = (
            await c.post(
                "/api/v1/interactions/upload-eml",
                files={"file": ("bericht.eml", build_rich_eml(), "message/rfc822")},
                headers=headers,
            )
        ).json()["interaction"]
        assert row["body_markdown"] is not None

        edited = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"body_text": "Samengevat: akkoord."},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["body_markdown"] is None
        assert edited.json()["body_text"] == "Samengevat: akkoord."


async def test_upload_can_ask_for_the_ai_task_fill_in(client_for, monkeypatch) -> None:
    """#342: an upload is not a second-class email.

    "Laat schakl deze taak invullen" (#327) used to hang off the *review transition* — the one
    thing this source deliberately skips — so the offer was unreachable from here. Nobody
    decided that; it was inherited from where the call happened to live. The offer now hangs
    off filing an email onto a task, which is what all three sources actually do.
    """
    t = await make_tenant("eml-enrich")
    headers = await auth_cookie(t.user)
    offered: list[tuple[str, str]] = []

    async def _capture(ctx, interaction_id, task_id):  # noqa: ANN001, ARG001
        offered.append((str(interaction_id), str(task_id)))
        return object()

    async def _available(ctx) -> bool:  # noqa: ANN001, ARG001
        return True

    monkeypatch.setattr("app.modules.interactions.jobs.schedule_enrichment", _capture)
    monkeypatch.setattr("app.modules.interactions.enrich.available", _available)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Offerte nakijken", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        uploaded = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(),
            data={"task_id": task["id"], "enrich_task": "true"},
            headers=headers,
        )
        assert uploaded.status_code == 201, uploaded.text
        row = uploaded.json()["interaction"]
        assert offered == [(row["id"], task["id"])]

    # Not asking still means not asking — the offer is opt-in, exactly as on approve.
    offered.clear()
    async with client_for(t.host) as c:
        again = await c.post(
            "/api/v1/interactions/upload-eml",
            files=_upload(message_id="<second@mail.client.nl>"),
            data={"task_id": task["id"]},
            headers=headers,
        )
        assert again.status_code == 201, again.text
        assert offered == []
