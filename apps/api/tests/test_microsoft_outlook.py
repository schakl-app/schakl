"""microsoft.outlook: the cursor poll, the shared gates on Graph shapes, the review flow,
the manual importer and the refresh button — against a URL-routed stub Graph."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.events import SystemContext
from app.core.mailbox.gates import SkipReason
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.models import MicrosoftConnection, MicrosoftSettings
from app.integrations.microsoft.oauth import SCOPE_IDENTITY, SCOPE_MAIL
from app.integrations.microsoft.outlook import manual, matching
from app.integrations.microsoft.outlook.matching import WellKnownFolders
from app.integrations.microsoft.outlook.models import OutlookSkip, OutlookSuppression
from app.integrations.microsoft.outlook.service import (
    SKIP_RETENTION_DAYS,
    classify,
    fetch_body,
    poll_connection,
    reap_skips,
)
from app.modules.interactions import system as interactions_system
from app.modules.interactions.models import Interaction, InteractionReviewer
from tests.conftest import auth_cookie, make_tenant

FOLDERS = {
    "junkemail": "f-junk",
    "deleteditems": "f-del",
    "drafts": "f-drafts",
    "sentitems": "f-sent",
}
INBOX = "f-inbox"


class _StubResponse:
    def __init__(self, status_code: int = 200, body=None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _StubGraph:
    """URL-routed Graph: well-known folders, message listings (filter / search), one message,
    its attachments. Records every listing's query so a test can assert what was asked."""

    def __init__(
        self,
        messages: dict[str, dict],
        attachments: dict[str, list] | None = None,
        attachment_bytes: dict[str, bytes] | None = None,
    ) -> None:
        self.messages = messages
        self.attachments = attachments or {}
        self.attachment_bytes = attachment_bytes or {}
        self.filters: list[str] = []
        self.searches: list[str] = []
        self.body_fetches: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs) -> _StubResponse:
        params = dict(kwargs.get("params") or {})
        if url.startswith("http"):
            parsed = urlparse(url)
            url = parsed.path.split("/v1.0", 1)[-1]
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.calls.append((url, params))
        if url.startswith("/me/mailFolders/"):
            name = url.rsplit("/", 1)[-1]
            return _StubResponse(200, {"id": FOLDERS[name]})
        if url == "/me/messages":
            if "$search" in params:
                self.searches.append(params["$search"])
                return _StubResponse(200, {"value": list(self.messages.values())})
            flt = params.get("$filter", "")
            self.filters.append(flt)
            rows = list(self.messages.values())
            if flt.startswith("receivedDateTime ge "):
                since = flt.split(" ge ", 1)[1]
                rows = [m for m in rows if m["receivedDateTime"] >= since]
                rows.sort(key=lambda m: m["receivedDateTime"])
            elif flt.startswith("internetMessageId eq "):
                wanted = flt.split(" eq ", 1)[1].strip("'").replace("''", "'")
                rows = [m for m in rows if m.get("internetMessageId") == wanted]
            elif flt.startswith("conversationId eq "):
                wanted = flt.split(" eq ", 1)[1].strip("'")
                rows = [m for m in rows if m.get("conversationId") == wanted]
            return _StubResponse(200, {"value": rows})
        if url.endswith("/$value"):
            attachment_id = url.rsplit("/", 2)[-2]
            data = self.attachment_bytes.get(attachment_id)
            return _StubResponse(200, {}, content=data) if data is not None else _StubResponse(404)
        if url.endswith("/attachments"):
            message_id = url.rsplit("/", 2)[-2]
            return _StubResponse(200, {"value": self.attachments.get(message_id, [])})
        if url.startswith("/me/messages/"):
            message_id = url.rsplit("/", 1)[-1]
            message = self.messages.get(message_id)
            if message is None:
                return _StubResponse(404)
            if "body" in params.get("$select", ""):
                self.body_fetches.append(message_id)
            return _StubResponse(200, message)
        raise AssertionError(f"unexpected Graph call: {url}")


def _stub_acting_as(stub: _StubGraph):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


def _patch(monkeypatch, stub: _StubGraph) -> None:
    factory = _stub_acting_as(stub)
    monkeypatch.setattr("app.integrations.microsoft.outlook.service.acting_as", factory)
    monkeypatch.setattr("app.integrations.microsoft.outlook.manual.acting_as", factory)


def _addr(value: str) -> dict:
    name, _, address = value.rpartition("<")
    if address:
        return {"emailAddress": {"name": name.strip() or None, "address": address.rstrip(">")}}
    return {"emailAddress": {"address": value}}


def _message(
    message_id: str,
    *,
    sender: str,
    to: str | list[str] = "me@agency.nl",
    cc: list[str] | None = None,
    subject: str = "Offerte",
    conversation: str = "conv-1",
    rfc822: str | None = None,
    received: str = "2026-07-12T10:00:00Z",
    folder: str = INBOX,
    categories: list[str] | None = None,
    is_draft: bool = False,
    body: dict | None = None,
    has_attachments: bool = False,
) -> dict:
    to_list = [to] if isinstance(to, str) else to
    message = {
        "id": message_id,
        "conversationId": conversation,
        "internetMessageId": rfc822 or f"<{message_id}@mail>",
        "subject": subject,
        "bodyPreview": f"{subject}...",
        "receivedDateTime": received,
        "sentDateTime": received,
        "from": _addr(sender),
        "toRecipients": [_addr(a) for a in to_list],
        "ccRecipients": [_addr(a) for a in (cc or [])],
        "categories": categories or [],
        "isDraft": is_draft,
        "parentFolderId": folder,
        "webLink": f"https://outlook.office.com/mail/id/{message_id}",
        "hasAttachments": has_attachments,
    }
    if body is not None:
        message["body"] = body
    return message


async def _seed(
    tenant,
    *,
    approval_mode: str = "approval_required",
    baselined: bool = True,
    excluded_category: str | None = None,
) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            MicrosoftSettings(
                org_id=tenant.org.id, outlook_enabled=True, outlook_approval_mode=approval_mode
            )
        )
        connection = MicrosoftConnection(
            org_id=tenant.org.id,
            user_id=tenant.user.id,
            microsoft_oid="oid-1",
            email="me@agency.nl",
            scopes=[SCOPE_IDENTITY, SCOPE_MAIL],
            refresh_token_encrypted=encrypt("rt"),
            outlook_sync_enabled=True,
            outlook_excluded_category=excluded_category,
            outlook_cursor_at=datetime(2026, 7, 1, tzinfo=UTC) if baselined else None,
        )
        session.add(connection)
        await session.commit()
        return connection.id


async def _poll(tenant, connection_id, stub, monkeypatch) -> int:
    _patch(monkeypatch, stub)
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        logged = await poll_connection(session, tenant.org, connection)
        await session.commit()
        return logged


async def _client_contact(c, headers, email: str = "klant@client.nl") -> dict:
    company = (
        await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
    ).json()
    await c.post(
        "/api/v1/contacts",
        json={"first_name": "Klant", "email": email, "company_ids": [company["id"]]},
        headers=headers,
    )
    return company


async def _classify(tenant, connection_id, message: dict, folders=None):
    from app.integrations.microsoft.oauth import microsoft_settings_row
    from app.integrations.microsoft.outlook.service import _internals

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        settings_row = await microsoft_settings_row(session, tenant.org.id)
        internals = await _internals(session, tenant.org.id)
        return await classify(
            session,
            tenant.org,
            connection,
            settings_row,
            message,
            folders
            or WellKnownFolders(junk="f-junk", deleted="f-del", drafts="f-drafts", sent="f-sent"),
            internals,
        )


# --------------------------------------------------------------------------- #
# Graph shapes
# --------------------------------------------------------------------------- #
def test_graph_message_shapes_read_as_the_shared_vocabulary() -> None:
    message = _message(
        "m1",
        sender="Klant <klant@client.nl>",
        to=["me@agency.nl"],
        cc=["cc@client.nl"],
        received="2026-07-12T10:00:00.1234567Z",
        categories=["Geen-CRM"],
    )
    participants = matching.participants_of(message)
    assert [(p["email"], p["role"]) for p in participants] == [
        ("klant@client.nl", "from"),
        ("me@agency.nl", "to"),
        ("cc@client.nl", "cc"),
    ]
    assert participants[0]["name"] == "Klant"
    assert matching.rfc822_id_of(message) == "<m1@mail>"
    assert matching.occurred_at_of(message) == datetime(2026, 7, 12, 10, 0, 0, 123456, tzinfo=UTC)
    folders = WellKnownFolders(junk="f-junk", deleted="f-del", drafts="f-drafts", sent="f-sent")
    assert matching.direction_of(message, folders) == "inbound"
    assert matching.direction_of({**message, "parentFolderId": "f-sent"}, folders) == "outbound"
    # A colleague's copy of our own outgoing mail is outbound whatever folder it sits in.
    assert matching.direction_of(message, folders, sender_internal=True) == "outbound"
    assert matching.is_not_a_message({**message, "isDraft": True}, folders)
    assert matching.is_not_a_message({**message, "parentFolderId": "f-junk"}, folders)
    assert not matching.is_not_a_message(message, folders)
    # The category matches loosely, and the message's own spelling is what is reported.
    assert matching.excluded_category_on(message, "geen-crm") == "Geen-CRM"
    assert matching.excluded_category_on(message, "other") is None


def test_parse_reference_accepts_an_id_a_web_link_and_a_message_id() -> None:
    graph_id = "AAMkAGI2THVSAAA=" + "x" * 20
    assert manual.parse_reference(graph_id) == manual.Reference(kind="id", value=graph_id)
    link = f"https://outlook.office.com/mail/inbox/id/{graph_id.replace('=', '%3D')}"
    assert manual.parse_reference(link).value == graph_id
    assert manual.parse_reference("<abc@prospect.nl>") == manual.Reference(
        kind="rfc822", value="<abc@prospect.nl>"
    )
    # Without brackets is the same reference; the wire form carries them (Graph stores them).
    assert manual.parse_reference("abc@prospect.nl").value == "<abc@prospect.nl>"
    for junk in ("", "hello", "https://outlook.office.com/mail/inbox", "https://example.com/id/x"):
        try:
            manual.parse_reference(junk)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError(f"accepted {junk!r}")


def test_search_fields_become_kql_and_never_operators() -> None:
    from datetime import date

    query = manual.OutlookSearchQuery(
        participant='klant@client.nl" OR received>=2000',
        subject="offerte (concept)",
        after=date(2026, 7, 1),
        before=date(2026, 7, 31),
    )
    kql = manual.build_search_query(query)
    assert kql == (
        'participants:"klant@client.nl OR received>=2000" AND subject:"offerte concept" '
        "AND received>=2026-07-01 AND received<=2026-07-31"
    )
    assert manual.build_search_query(manual.OutlookSearchQuery()) == ""


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #
async def test_first_poll_baselines_without_backfill(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-baseline")
    connection_id = await _seed(t, baselined=False)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
    stub = _StubGraph({"m1": _message("m1", sender="klant@client.nl")})
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    # No listing was asked for: connecting a mailbox is opt-in going forward.
    assert stub.filters == []
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        assert connection.outlook_cursor_at is not None
        assert (await session.execute(select(Interaction))).first() is None


async def test_poll_matches_contact_and_logs_pending_with_reviewers(
    client_for, monkeypatch
) -> None:
    from tests.test_notification_channels import _member

    t = await make_tenant("outlook-poll")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _client_contact(c, headers)
        colleague = await _member(c, headers, "collega@outlook-poll-example.nl")

    stub = _StubGraph(
        {
            "m1": _message(
                "m1",
                sender="Klant <klant@client.nl>",
                to="me@agency.nl",
                cc=["collega@outlook-poll-example.nl"],
                received="2026-07-12T10:00:00Z",
            ),
            "m-nomatch": _message(
                "m-nomatch",
                sender="onbekend@elders.nl",
                conversation="conv-2",
                received="2026-07-12T11:00:00Z",
            ),
        }
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    # The listing carried the cursor (minus its overlap), never the whole mailbox.
    assert stub.filters and stub.filters[0].startswith("receivedDateTime ge 2026-06-30T23:55:00Z")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.kind == "email" and row.status == "pending" and row.source == "outlook"
        assert row.company_id == uuid.UUID(company["id"])
        assert row.gmail_message_id == "m1" and row.gmail_thread_id == "conv-1"
        assert row.rfc822_message_id == "<m1@mail>"
        assert row.deep_link == "https://outlook.office.com/mail/id/m1"
        assert row.snippet == "Offerte..."
        assert row.body_text is None  # metadata-first: no content before approval
        assert row.direction == "inbound"
        assert [p["email"] for p in row.participants] == [
            "klant@client.nl",
            "me@agency.nl",
            "collega@outlook-poll-example.nl",
        ]
        reviewers = (await session.execute(select(InteractionReviewer))).scalars().all()
        assert [r.user_id for r in reviewers] == [colleague.id]
        connection = await session.get(MicrosoftConnection, connection_id)
        assert connection.outlook_cursor_at == datetime(2026, 7, 12, 11, tzinfo=UTC)
        assert connection.outlook_last_polled_at is not None

        from app.modules.notifications.models import NotificationEvent

        events = (
            (
                await session.execute(
                    select(NotificationEvent).where(
                        NotificationEvent.event_type == "interactions.email_pending"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1 and events[0].payload.get("subject") == "Offerte"

    # A second poll over the same window is a no-op: the row is already here.
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert len((await session.execute(select(Interaction))).scalars().all()) == 1


async def test_unknown_sender_drafts_junk_and_categories_are_declined_by_name(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("outlook-gates")
    connection_id = await _seed(t, excluded_category="geen-crm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)

    stranger = await _classify(t, connection_id, _message("m1", sender="onbekend@elders.nl"))
    assert stranger.reason is SkipReason.NO_EXTERNAL_MATCH
    draft = await _classify(
        t, connection_id, _message("m2", sender="klant@client.nl", is_draft=True)
    )
    assert draft.reason is SkipReason.NOT_A_MESSAGE
    junk = await _classify(
        t, connection_id, _message("m3", sender="klant@client.nl", folder="f-junk")
    )
    assert junk.reason is SkipReason.NOT_A_MESSAGE
    tagged = await _classify(
        t, connection_id, _message("m4", sender="klant@client.nl", categories=["Geen-CRM"])
    )
    assert tagged.reason is SkipReason.EXCLUDED_CATEGORY
    assert tagged.detail == {"label": "Geen-CRM"}
    # And the message that passes every gate says where it files.
    fine = await _classify(t, connection_id, _message("m5", sender="klant@client.nl"))
    assert fine.logs and fine.pending and fine.mappings["company_id"] is not None

    # None of those policy skips left a row behind — only the two failures ever do.
    stub = _StubGraph(
        {
            "m1": _message("m1", sender="onbekend@elders.nl"),
            "m4": _message("m4", sender="klant@client.nl", categories=["Geen-CRM"]),
        }
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.execute(select(Interaction))).first() is None
        assert (await session.execute(select(OutlookSkip))).first() is None


async def test_auto_approve_logs_the_html_body_and_inlines_the_logo(
    client_for, monkeypatch, tmp_path
) -> None:
    from app.config import settings
    from app.core.storage.models import StoredFile

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("outlook-auto")
    connection_id = await _seed(t, approval_mode="auto_approve")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)

    html = '<p>Hallo <b>daar</b></p><img src="cid:logo@client" alt="logo">'
    stub = _StubGraph(
        {
            "m1": _message(
                "m1",
                sender="klant@client.nl",
                has_attachments=True,
                body={"contentType": "html", "content": html},
            )
        },
        attachments={
            "m1": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-logo",
                    "name": "logo.png",
                    "contentType": "image/png",
                    "size": 4,
                    "isInline": True,
                    "contentId": "logo@client",
                },
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-pdf",
                    "name": "offerte.pdf",
                    "contentType": "application/pdf",
                    "size": 9,
                    "isInline": False,
                    "contentId": None,
                },
                {"@odata.type": "#microsoft.graph.itemAttachment", "id": "att-item", "name": "x"},
            ]
        },
        attachment_bytes={"att-logo": b"\x89PNG", "att-pdf": b"%PDF-fake"},
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    assert stub.body_fetches == ["m1"]

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.status == "logged"
        assert row.body_text and "daar" in row.body_text and "<" not in row.body_text
        assert row.body_markdown and "**daar**" in row.body_markdown
        stored = (await session.execute(select(StoredFile))).scalars().all()
        by_name = {f.filename: f for f in stored}
        assert set(by_name) == {"logo.png", "offerte.pdf"}
        assert by_name["logo.png"].content_id == "logo@client"
        assert by_name["offerte.pdf"].content_id is None
        # The body's cid: marker now names the stored file.
        assert f"file:{by_name['logo.png'].id}" in row.body_markdown

        # A sweep re-run stores nothing twice.
        assert await fetch_body(session, t.org, row.id) is True
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert len((await session.execute(select(StoredFile))).scalars().all()) == 2


async def test_a_colleagues_mailbox_already_logged_it(client_for, monkeypatch) -> None:
    """The cross-mailbox dedup reads the global Message-ID, whichever provider logged it."""
    t = await make_tenant("outlook-dedup")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await interactions_system.record_email(
            SystemContext(org=t.org, session=session),
            owner_user_id=t.user.id,
            owner_name="Me",
            occurred_at=datetime.now(UTC),
            subject="Offerte",
            snippet=None,
            direction="inbound",
            participants=[{"email": "klant@client.nl", "name": None, "role": "from"}],
            gmail_message_id="gmail-hex-1",
            gmail_thread_id="thr-1",
            rfc822_message_id="<shared@mail>",
            deep_link=None,
            pending=False,
            mappings={},
            source="gmail",
        )
        await session.commit()
    decision = await _classify(
        t, connection_id, _message("m1", sender="klant@client.nl", rfc822="<shared@mail>")
    )
    assert decision.reason is SkipReason.LOGGED_ELSEWHERE
    stub = _StubGraph({"m1": _message("m1", sender="klant@client.nl", rfc822="<shared@mail>")})
    assert await _poll(t, connection_id, stub, monkeypatch) == 0


async def test_a_copy_defers_to_the_colleagues_syncing_mailbox_and_leaves_a_row(
    client_for, monkeypatch
) -> None:
    from tests.test_notification_channels import _member

    t = await make_tenant("outlook-defer")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
        colleague = await _member(c, headers, "luka@outlook-defer-example.nl")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            MicrosoftConnection(
                org_id=t.org.id,
                user_id=colleague.id,
                microsoft_oid="oid-luka",
                email="luka@outlook-defer-example.nl",
                scopes=[SCOPE_IDENTITY, SCOPE_MAIL],
                refresh_token_encrypted=encrypt("rt"),
                outlook_sync_enabled=True,
                outlook_cursor_at=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    # Incoming, addressed to Luka, this mailbox merely in Cc: Luka's own mailbox logs it.
    message = _message(
        "m-cc", sender="klant@client.nl", to="luka@outlook-defer-example.nl", cc=["me@agency.nl"]
    )
    stub = _StubGraph({"m-cc": message})
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.execute(select(Interaction))).first() is None
        skip = (await session.execute(select(OutlookSkip))).scalar_one()
        assert skip.message_id == "m-cc" and skip.conversation_id == "conv-1"
        assert skip.reason == "deferred_to_owner"
        assert skip.detail == {"owner": "luka@outlook-defer-example.nl"}
        # Re-offered on the next poll (the cursor overlap does that), still one row.
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert len((await session.execute(select(OutlookSkip))).scalars().all()) == 1
        # And the reaper takes it once it is older than the window.
        skip = (await session.execute(select(OutlookSkip))).scalar_one()
        skip.created_at = datetime.now(UTC) - timedelta(days=SKIP_RETENTION_DAYS + 1)
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await reap_skips(t.org, session)
        await session.commit()
        assert (await session.execute(select(OutlookSkip))).first() is None

    # The sender's own copy is never deferred, wherever it sits: our mail in Sent Items.
    sent = _message("m-sent", sender="me@agency.nl", to="klant@client.nl", folder="f-sent")
    decision = await _classify(t, connection_id, sent)
    assert decision.logs


async def test_the_cursor_advances_and_never_moves_backwards(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-cursor")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        connection.outlook_cursor_at = datetime(2026, 7, 20, tzinfo=UTC)
        await session.commit()
    # Everything the stub holds is older than the cursor: nothing listed, cursor unchanged.
    stub = _StubGraph(
        {"old": _message("old", sender="klant@client.nl", received="2026-07-12T10:00:00Z")}
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        assert connection.outlook_cursor_at == datetime(2026, 7, 20, tzinfo=UTC)
    # A newer message moves it forward to that message's instant.
    stub = _StubGraph(
        {"new": _message("new", sender="klant@client.nl", received="2026-07-21T09:30:00Z")}
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        assert connection.outlook_cursor_at == datetime(2026, 7, 21, 9, 30, tzinfo=UTC)


async def test_poison_message_does_not_wedge_the_poll(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-poison")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
    good = _message("good", sender="klant@client.nl", received="2026-07-12T12:00:00Z")
    poison = _message("poison", sender="klant@client.nl", received="2026-07-12T11:00:00Z")
    poison["toRecipients"] = "not-a-list"  # a shape the ingest cannot read
    stub = _StubGraph({"poison": poison, "good": good})
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.gmail_message_id == "good"
        skip = (await session.execute(select(OutlookSkip))).scalar_one()
        assert skip.message_id == "poison" and skip.reason == "ingest_error"


# --------------------------------------------------------------------------- #
# The review flow, over HTTP
# --------------------------------------------------------------------------- #
async def test_approve_offers_the_body_fetch_and_reject_suppresses(client_for, monkeypatch) -> None:
    enqueued: list[tuple] = []

    async def _capture(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        enqueued.append((function, args))

    monkeypatch.setattr("app.core.jobs.enqueue", _capture)
    t = await make_tenant("outlook-review")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
        stub = _StubGraph(
            {
                "m-a": _message(
                    "m-a",
                    sender="klant@client.nl",
                    conversation="conv-a",
                    received="2026-07-12T10:00:00Z",
                ),
                "m-r": _message(
                    "m-r",
                    sender="klant@client.nl",
                    conversation="conv-r",
                    received="2026-07-12T10:05:00Z",
                ),
            }
        )
        assert await _poll(t, connection_id, stub, monkeypatch) == 2
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            rows = {
                r.gmail_message_id: r.id
                for r in (await session.execute(select(Interaction))).scalars()
            }

        listed = (
            await c.get("/api/v1/interactions?status=pending&mine=true", headers=headers)
        ).json()
        assert all(item["reviewable"] for item in listed["items"])

        approved = await c.post(f"/api/v1/interactions/{rows['m-a']}/approve", headers=headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "logged"
        assert [e for e in enqueued if e[0] == "outlook_fetch_body"] == [
            ("outlook_fetch_body", (str(t.org.id), str(rows["m-a"])))
        ]
        # The Gmail feed's subscriber did not claim it.
        assert not [e for e in enqueued if e[0] == "google_gmail_fetch_body"]

        rejected = await c.post(
            f"/api/v1/interactions/{rows['m-r']}/reject",
            json={"suppress_thread": True},
            headers=headers,
        )
        assert rejected.status_code == 204, rejected.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        remaining = (await session.execute(select(Interaction))).scalars().all()
        assert [r.gmail_message_id for r in remaining] == ["m-a"]
        suppressions = (await session.execute(select(OutlookSuppression))).scalars().all()
        assert {(s.message_id, s.conversation_id) for s in suppressions} == {
            ("m-r", None),
            (None, "conv-r"),
        }

    # The rejected message and a follow-up in its conversation stay out.
    stub2 = _StubGraph(
        {
            "m-r": _message("m-r", sender="klant@client.nl", conversation="conv-r"),
            "m-r2": _message(
                "m-r2",
                sender="klant@client.nl",
                conversation="conv-r",
                received="2026-07-12T10:10:00Z",
            ),
        }
    )
    assert await _poll(t, connection_id, stub2, monkeypatch) == 0
    decision = await _classify(t, connection_id, stub2.messages["m-r2"])
    assert decision.reason is SkipReason.SUPPRESSED_THREAD


async def test_worker_body_fetch_only_touches_its_own_rows(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-fetch")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
    stub = _StubGraph(
        {
            "m1": _message(
                "m1", sender="klant@client.nl", body={"contentType": "text", "content": "Plat."}
            )
        }
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert await fetch_body(session, t.org, row.id) is True
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.body_text == "Plat." and row.body_markdown is None
        # A gmail row handed to this fetcher is declined without a Graph call.
        gmail_row = await interactions_system.record_email(
            SystemContext(org=t.org, session=session),
            owner_user_id=t.user.id,
            owner_name="Me",
            occurred_at=datetime.now(UTC),
            subject="G",
            snippet=None,
            direction="inbound",
            participants=[],
            gmail_message_id="hex1",
            gmail_thread_id=None,
            rfc822_message_id=None,
            deep_link=None,
            pending=False,
            mappings={},
            source="gmail",
        )
        assert await fetch_body(session, t.org, gmail_row.id) is False
        assert stub.body_fetches == ["m1"]


# --------------------------------------------------------------------------- #
# The refresh button
# --------------------------------------------------------------------------- #
async def test_manual_refresh_polls_once_and_then_cools_down(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-refresh")
    await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers)
        stub = _StubGraph({"m1": _message("m1", sender="klant@client.nl")})
        polls: list = []
        factory = _stub_acting_as(stub)

        @asynccontextmanager
        async def _counting(session, org, connection):  # noqa: ANN001
            polls.append(connection.id)
            async with factory(session, org, connection) as inner:
                yield inner

        monkeypatch.setattr("app.integrations.microsoft.outlook.service.acting_as", _counting)

        first = await c.post("/api/v1/microsoft/outlook/refresh", headers=headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "polled" and body["logged"] == 1
        assert body["sync"]["available"] is True and body["sync"]["outlook_enabled"] is True
        assert body["sync"]["retry_after_seconds"] > 0

        second = await c.post("/api/v1/microsoft/outlook/refresh", headers=headers)
        assert second.status_code == 200
        assert second.json()["status"] == "cooldown" and second.json()["logged"] == 0
        assert len(polls) == 1

        status = (await c.get("/api/v1/microsoft/outlook/status", headers=headers)).json()
        assert status["available"] is True
        assert status["last_polled_at"] == body["sync"]["last_polled_at"]


async def test_manual_refresh_is_refused_when_the_mailbox_is_not_syncing(client_for) -> None:
    t = await make_tenant("outlook-refresh-off")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        connection.outlook_sync_enabled = False
        await session.commit()
    async with client_for(t.host) as c:
        refused = await c.post("/api/v1/microsoft/outlook/refresh", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.outlook_sync_off"
        status = (await c.get("/api/v1/microsoft/outlook/status", headers=headers)).json()
        assert status["connected"] is True and status["available"] is False


# --------------------------------------------------------------------------- #
# The manual importer
# --------------------------------------------------------------------------- #
def _manual_stub() -> _StubGraph:
    return _StubGraph(
        {
            "msg-a": _message(
                "msg-a",
                sender="Nieuw <nieuw@prospect.nl>",
                subject="Offerteaanvraag",
                conversation="conv-9",
                rfc822="<aanvraag-1@prospect.nl>",
                received="2026-07-12T09:00:00Z",
                body={"contentType": "text", "content": "Graag een offerte."},
            ),
            "msg-b": _message(
                "msg-b",
                sender="me@agency.nl",
                to="nieuw@prospect.nl",
                subject="Re: Offerteaanvraag",
                conversation="conv-9",
                rfc822="<antwoord-1@agency.nl>",
                folder="f-sent",
                received="2026-07-12T10:00:00Z",
                body={"contentType": "text", "content": "Komt eraan."},
            ),
        }
    )


async def test_lookup_widens_to_the_conversation_and_import_logs_it(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("outlook-manual")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    _patch(monkeypatch, stub)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Prospect BV"}, headers=headers)
        ).json()
        found = await c.get(
            "/api/v1/microsoft/outlook/lookup",
            params={"reference": "aanvraag-1@prospect.nl"},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        body = found.json()
        assert [m["message_id"] for m in body["messages"]] == ["msg-a", "msg-b"]
        assert body["widened_to_thread"] is True and body["thread_id"] == "conv-9"
        assert "internetMessageId eq '<aanvraag-1@prospect.nl>'" in stub.filters
        candidate = body["messages"][0]
        assert candidate["subject"] == "Offerteaanvraag"
        assert candidate["from_email"] == "nieuw@prospect.nl" and candidate["from_name"] == "Nieuw"
        assert candidate["direction"] == "inbound"
        assert body["messages"][1]["direction"] == "outbound"
        assert candidate["logged"] is False and candidate["interaction_id"] is None
        # A prospect nobody has made a contact of yet is exactly what the gate declines.
        assert candidate["skip_reason"] == "no_external_match"

        created = await c.post(
            "/api/v1/microsoft/outlook/import",
            json={"message_id": "msg-a", "company_id": company["id"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        result = created.json()
        assert result["subject"] == "Offerteaanvraag" and result["body_fetched"] is True

        detail = (
            await c.get(f"/api/v1/interactions/{result['interaction_id']}", headers=headers)
        ).json()
        assert detail["status"] == "logged" and detail["source"] == "outlook"
        assert detail["company_id"] == company["id"]
        assert detail["deep_link"] == "https://outlook.office.com/mail/id/msg-a"
        assert detail["gmail_thread_id"] == "conv-9"

        # The conversation read marks what is on the timeline now.
        thread = (await c.get("/api/v1/microsoft/outlook/threads/conv-9", headers=headers)).json()
        assert [(m["message_id"], m["logged"]) for m in thread["messages"]] == [
            ("msg-a", True),
            ("msg-b", False),
        ]
        assert thread["messages"][0]["interaction_id"] == result["interaction_id"]

        # The same message twice in one mailbox is never what anybody meant.
        repeat = await c.post(
            "/api/v1/microsoft/outlook/import",
            json={"message_id": "msg-a", "company_id": company["id"]},
            headers=headers,
        )
        assert repeat.status_code == 409

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.body_text == "Graag een offerte."


async def test_search_runs_the_kql_and_explains_every_candidate(client_for, monkeypatch) -> None:
    t = await make_tenant("outlook-search")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    _patch(monkeypatch, stub)
    async with client_for(t.host) as c:
        empty = await c.get("/api/v1/microsoft/outlook/search", headers=headers)
        assert empty.status_code == 422
        assert empty.json()["error"]["message"] == "errors.outlook_search_empty"

        found = await c.get(
            "/api/v1/microsoft/outlook/search",
            params={"participant": "nieuw@prospect.nl", "after": "2026-07-01"},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        body = found.json()
        assert body["query"] == 'participants:"nieuw@prospect.nl" AND received>=2026-07-01'
        assert stub.searches == ['"participants:\\"nieuw@prospect.nl\\" AND received>=2026-07-01"']
        assert [m["message_id"] for m in body["messages"]] == ["msg-a", "msg-b"]
        assert all(m["skip_reason"] == "no_external_match" for m in body["messages"])

        # A garbage reference is refused with the key that names what does work.
        bad = await c.get(
            "/api/v1/microsoft/outlook/lookup", params={"reference": "hello"}, headers=headers
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["message"] == "errors.outlook_reference_unreadable"


async def test_manual_reads_refuse_a_mailbox_that_is_not_ours_to_read(client_for) -> None:
    t = await make_tenant("outlook-manual-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # No settings row: the surface is off for this org.
        off = await c.get(
            "/api/v1/microsoft/outlook/lookup", params={"reference": "x@y.nl"}, headers=headers
        )
        assert off.status_code == 409
        assert off.json()["error"]["message"] == "errors.outlook_disabled"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(MicrosoftSettings(org_id=t.org.id, outlook_enabled=True))
        await session.commit()
    async with client_for(t.host) as c:
        unconnected = await c.get(
            "/api/v1/microsoft/outlook/lookup", params={"reference": "x@y.nl"}, headers=headers
        )
        assert unconnected.status_code == 409
        assert unconnected.json()["error"]["message"] == "errors.microsoft_not_connected"


async def test_outlook_rows_are_tenant_isolated(client_for, monkeypatch) -> None:
    a = await make_tenant("outlook-iso-a")
    b = await make_tenant("outlook-iso-b")
    connection_id = await _seed(a)
    headers_a = await auth_cookie(a.user)
    async with client_for(a.host) as c:
        await _client_contact(c, headers_a)
    stub = _StubGraph({"m1": _message("m1", sender="klant@client.nl")})
    assert await _poll(a, connection_id, stub, monkeypatch) == 1

    headers_b = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/interactions", headers=headers_b)).json()["items"] == []
        status = (await cb.get("/api/v1/microsoft/outlook/status", headers=headers_b)).json()
        assert status["connected"] is False and status["outlook_enabled"] is False
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        assert (await session.execute(select(Interaction))).first() is None
        assert (await session.execute(select(MicrosoftConnection))).first() is None
