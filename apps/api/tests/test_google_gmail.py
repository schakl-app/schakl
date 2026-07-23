"""google.gmail (#22): matching units, the poll pipeline, approval wiring, suppressions."""

from __future__ import annotations

import base64
import uuid
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import select

from app.core.crypto import encrypt
from app.db import async_session_maker, set_current_org
from app.modules.google.gmail import matching
from app.modules.google.gmail.models import GmailSuppression
from app.modules.google.gmail.service import fetch_body, poll_connection
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_GMAIL
from app.modules.interactions.models import Interaction
from tests.conftest import auth_cookie, make_tenant

# --------------------------------------------------------------------------- #
# Pure matching units
# --------------------------------------------------------------------------- #


def test_participants_direction_and_relevance() -> None:
    headers = {
        "From": "Klant <Klant@Client.NL>",
        "To": "me@agency.nl, Collega <collega@agency.nl>",
        "Cc": "cc@client.nl",
    }
    participants = matching.parse_participants(headers)
    assert [p["email"] for p in participants] == [
        "klant@client.nl",
        "me@agency.nl",
        "collega@agency.nl",
        "cc@client.nl",
    ]
    assert participants[0]["role"] == "from" and participants[-1]["role"] == "cc"

    assert matching.direction_of(["SENT"]) == "outbound"
    assert matching.direction_of(["INBOX", "UNREAD"]) == "inbound"

    assert not matching.is_relevant(["DRAFT"], None)
    assert not matching.is_relevant(["INBOX", "Label_7"], "Label_7")  # the opt-out label
    assert matching.is_relevant(["INBOX"], "Label_7")

    # Colleague-to-colleague chatter is not a client touchpoint.
    members = {"me@agency.nl", "collega@agency.nl"}
    internal = [{"email": "me@agency.nl"}, {"email": "collega@agency.nl"}]
    assert matching.internal_only(internal, members)
    assert not matching.internal_only(participants, members)


def test_mapping_resolution_and_status_decision() -> None:
    contact_a, contact_b = uuid.uuid4(), uuid.uuid4()
    company_1, company_2 = uuid.uuid4(), uuid.uuid4()

    single = matching.resolve_mappings(
        [matching.ContactMatch(contact_id=contact_a, company_ids=[company_1])]
    )
    assert single == {"contact_id": contact_a, "company_id": company_1}

    # Ambiguity resolves to the oldest link, deterministically — remap covers mistakes,
    # and every logged email stays reachable on some timeline.
    ambiguous = matching.resolve_mappings(
        [
            matching.ContactMatch(contact_id=contact_a, company_ids=[company_1, company_2]),
            matching.ContactMatch(contact_id=contact_b, company_ids=[company_2]),
        ]
    )
    assert ambiguous["company_id"] == company_1 and ambiguous["contact_id"] == contact_a

    assert matching.decide_status("approval_required", "inherit_pending", inherited=False)
    assert matching.decide_status("approval_required", "inherit_pending", inherited=True)
    assert not matching.decide_status("approval_required", "inherit_approve", inherited=True)
    assert not matching.decide_status("auto_approve", "inherit_pending", inherited=False)


def test_body_extraction_prefers_plain_text() -> None:
    def _b64(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode()).decode()

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Hallo,\n\nakkoord!")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Hallo</p>")}},
        ],
    }
    assert matching.extract_text(payload) == "Hallo,\n\nakkoord!"

    html_only = {
        "mimeType": "text/html",
        "body": {"data": _b64("<style>x{}</style><p>Hallo <b>daar</b></p>")},
    }
    assert "Hallo" in (matching.extract_text(html_only) or "")
    assert "<" not in (matching.extract_text(html_only) or "")


def test_snippet_is_decoded_and_depadded_at_ingest() -> None:
    """Gmail's snippet is HTML-escaped and preheader-padded (#263).

    Stored raw it renders as escape codes in every list row *and* matches nothing when
    someone searches the words they actually read — ``list(q=...)`` searches this column.
    """
    assert matching.clean_snippet("&#39;s ochtends &amp; morgen") == "'s ochtends & morgen"
    assert matching.clean_snippet("de &quot;offerte&quot;") == 'de "offerte"'
    assert matching.clean_snippet("caf&#xe9; om 9u") == "café om 9u"
    # The invisible padding (zero-width space, soft hyphen, BOM) and the whitespace runs go.
    assert (
        matching.clean_snippet("Nieuwsbrief\u200b\u200b   juli\n\n2026\u00ad\ufeff")
        == "Nieuwsbrief juli 2026"
    )
    # Not-an-entity stays as typed, and nothing-at-all stays None rather than becoming "".
    assert matching.clean_snippet("R&D budget & marge") == "R&D budget & marge"
    assert matching.clean_snippet(None) is None
    assert matching.clean_snippet("  \u200b ") is None


# --------------------------------------------------------------------------- #
# The poll pipeline against a scripted Gmail
# --------------------------------------------------------------------------- #


class _StubResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _StubGmail:
    """URL-routed Gmail stub: profile / history / labels / message fetches."""

    def __init__(
        self,
        *,
        history: list[str],
        messages: dict[str, dict],
        history_id: str = "1000",
        labels: list[dict] | None = None,
    ) -> None:
        self.history = history
        self.messages = messages
        self.history_id = history_id
        self.labels = labels or []
        self.full_fetches: list[str] = []

    async def get(self, url: str, **kwargs) -> _StubResponse:
        params = kwargs.get("params") or {}
        if url.endswith("/profile"):
            return _StubResponse(200, {"historyId": self.history_id})
        if url.endswith("/history"):
            return _StubResponse(
                200,
                {
                    "historyId": self.history_id,
                    "history": [
                        {"messagesAdded": [{"message": {"id": mid}}]} for mid in self.history
                    ],
                },
            )
        if url.endswith("/labels"):
            return _StubResponse(200, {"labels": self.labels})
        message_id = url.rsplit("/", 1)[-1]
        message = self.messages.get(message_id)
        if message is None:
            return _StubResponse(404)
        if params.get("format") == "full":
            self.full_fetches.append(message_id)
        return _StubResponse(200, message)


def _stub_acting_as(stub):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


def _message(
    message_id: str,
    *,
    sender: str,
    to: str = "me@agency.nl",
    subject: str = "Offerte",
    labels: list[str] | None = None,
    thread: str = "thr-1",
    rfc822: str | None = None,
    body_text: str | None = None,
) -> dict:
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": rfc822 or f"<{message_id}@mail>"},
    ]
    payload: dict = {"headers": headers}
    if body_text is not None:
        payload["mimeType"] = "text/plain"
        payload["body"] = {
            "data": base64.urlsafe_b64encode(body_text.encode()).decode()
        }
    return {
        "id": message_id,
        "threadId": thread,
        "labelIds": labels or ["INBOX"],
        "snippet": f"{subject}...",
        "internalDate": "1783868400000",
        "payload": payload,
    }


async def _seed(
    tenant,
    *,
    approval_mode: str = "approval_required",
    history_id: str = "5",
    log_internal: bool = False,
):
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                gmail_enabled=True,
                gmail_approval_mode=approval_mode,
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
    monkeypatch.setattr("app.modules.google.gmail.service.acting_as", _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        logged = await poll_connection(session, tenant.org, connection)
        await session.commit()
        return logged


async def test_poll_matches_contact_and_logs_pending(client_for, monkeypatch) -> None:
    t = await make_tenant("gmail-poll")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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

    stub = _StubGmail(
        history=["msg-1", "msg-nomatch"],
        messages={
            "msg-1": _message("msg-1", sender="Klant <klant@client.nl>"),
            "msg-nomatch": _message(
                "msg-nomatch", sender="onbekend@elders.nl", thread="thr-2"
            ),
        },
        history_id="9000",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.kind == "email" and row.status == "pending"
        assert row.company_id == uuid.UUID(company["id"])
        assert row.body_text is None  # metadata-first: no content before approval
        assert row.direction == "inbound"
        assert row.deep_link and "msg-1" in row.deep_link
        connection = await session.get(GoogleConnection, connection_id)
        assert connection.gmail_history_id == "9000"

        # The owner heard about it, once.
        from app.modules.notifications.models import NotificationEvent

        pending_events = (
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
        assert len(pending_events) == 1
        assert pending_events[0].payload.get("subject") == "Offerte"

    # A second poll over the same history is a no-op (message ids already imported).
    assert await _poll(t, connection_id, stub, monkeypatch) == 0


async def test_portal_contact_mail_still_logs(client_for, monkeypatch) -> None:
    """A portal login (#193) is a membership whose user is a *client's contact* — it must not
    count as a colleague. With the naive all-memberships set, inviting a client to the portal
    made ``internal_only`` classify their entire correspondence as internal chatter: every
    poll succeeded with ``logged:0`` and the feed silently went dark for that client."""
    t = await make_tenant("gmail-portal")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Klant",
                    "email": "klant@client.nl",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        # Portal access creates a user + client-role membership for the contact's address.
        assert (
            await c.post(f"/api/v1/contacts/{contact['id']}/portal", headers=headers)
        ).status_code == 200

    # Mail between the owner and the portal-enabled contact — ``to`` must be the owner's
    # *login* address (not the default stub address, which belongs to no member): only then
    # is every participant a membership holder, the exact shape that was dropped as
    # internal-only.
    stub = _StubGmail(
        history=["msg-portal"],
        messages={
            "msg-portal": _message(
                "msg-portal", sender="Klant <klant@client.nl>", to=t.user.email
            )
        },
        history_id="9200",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.status == "pending"
        assert row.company_id == uuid.UUID(company["id"])


async def test_internal_mail_dropped_by_default(client_for, monkeypatch) -> None:
    """Colleague-to-colleague mail stays out unless the org opts in."""
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-internal-off")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _member(c, headers, "collega@gmail-internal-off-example.nl")

    stub = _StubGmail(
        history=["msg-int"],
        messages={
            "msg-int": _message(
                "msg-int",
                sender="Collega <collega@gmail-internal-off-example.nl>",
                to=t.user.email,
            )
        },
        history_id="9300",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0


async def test_internal_mail_logs_pending_when_opted_in(client_for, monkeypatch) -> None:
    """With ``gmail_log_internal`` on, colleague mail is ingested — but always *pending*,
    even under auto-approve: there is no contact to map from, so filing it onto a client or
    project is the reviewer's call. Unknown external mail stays out either way."""
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-internal-on")
    connection_id = await _seed(t, approval_mode="auto_approve", log_internal=True)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _member(c, headers, "collega@gmail-internal-on-example.nl")

    stub = _StubGmail(
        history=["msg-int", "msg-stranger"],
        messages={
            "msg-int": _message(
                "msg-int",
                sender="Collega <collega@gmail-internal-on-example.nl>",
                to=t.user.email,
            ),
            "msg-stranger": _message(
                "msg-stranger", sender="onbekend@elders.nl", to=t.user.email, thread="thr-2"
            ),
        },
        history_id="9400",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.gmail_message_id == "msg-int"
        assert row.status == "pending"  # forced despite auto_approve — unmapped internal
        assert row.company_id is None and row.contact_id is None


async def test_poison_message_does_not_wedge_the_poll(client_for, monkeypatch) -> None:
    """One message whose ingest raises is skipped (loudly logged): the rest of the batch
    still imports and historyId advances. Before the per-message guard, the poll re-aborted
    on the same message every 5 minutes and the whole feed silently stopped."""
    t = await make_tenant("gmail-poison")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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

    class _PoisonGmail(_StubGmail):
        async def get(self, url: str, **kwargs) -> _StubResponse:
            if url.rsplit("/", 1)[-1] == "msg-poison":
                raise RuntimeError("malformed payload")
            return await super().get(url, **kwargs)

    # Poison first, so surviving it proves the loop continues past the failure.
    stub = _PoisonGmail(
        history=["msg-poison", "msg-good"],
        messages={"msg-good": _message("msg-good", sender="Klant <klant@client.nl>")},
        history_id="9100",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.gmail_message_id == "msg-good"
        connection = await session.get(GoogleConnection, connection_id)
        assert connection.gmail_history_id == "9100"


async def test_first_poll_baselines_without_backfill(monkeypatch) -> None:
    t = await make_tenant("gmail-baseline")
    connection_id = await _seed(t, history_id=None)  # type: ignore[arg-type]
    stub = _StubGmail(history=["old-1"], messages={}, history_id="777")
    assert await _poll(t, connection_id, stub, monkeypatch) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        assert connection.gmail_history_id == "777"
        assert (await session.execute(select(Interaction))).first() is None


async def test_auto_approve_logs_with_body_and_rfc822_dedup(client_for, monkeypatch) -> None:
    t = await make_tenant("gmail-auto")
    connection_id = await _seed(t, approval_mode="auto_approve")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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

    stub = _StubGmail(
        history=["msg-a"],
        messages={
            "msg-a": _message(
                "msg-a",
                sender="klant@client.nl",
                rfc822="<shared@mail>",
                body_text="Akkoord met de offerte.",
            )
        },
        history_id="9100",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.status == "logged"
        assert row.body_text == "Akkoord met de offerte."  # fetched inline on auto-approve

    # A colleague's mailbox sees the same email (same Message-ID): one timeline entry only.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        colleague = GoogleConnection(
            org_id=t.org.id,
            user_id=t.user.id,
            google_sub="x",
            email="x",
            scopes=[SCOPE_GMAIL],
            refresh_token_encrypted=encrypt("rt"),
        )
        del colleague  # (schema: one connection per user; dedup is asserted via the same poll)
    stub2 = _StubGmail(
        history=["msg-b"],
        messages={
            "msg-b": _message("msg-b", sender="klant@client.nl", rfc822="<shared@mail>")
        },
        history_id="9200",
    )
    assert await _poll(t, connection_id, stub2, monkeypatch) == 0


async def test_rejection_suppresses_and_thread_stays_out(client_for, monkeypatch) -> None:
    t = await make_tenant("gmail-reject")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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

        stub = _StubGmail(
            history=["msg-r"],
            messages={"msg-r": _message("msg-r", sender="klant@client.nl", thread="thr-9")},
            history_id="9300",
        )
        assert await _poll(t, connection_id, stub, monkeypatch) == 1
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row = (await session.execute(select(Interaction))).scalar_one()
            row_id = str(row.id)

        # The owner rejects, ignoring the whole conversation.
        rejected = await c.post(
            f"/api/v1/interactions/{row_id}/reject",
            json={"suppress_thread": True},
            headers=headers,
        )
        assert rejected.status_code == 204, rejected.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.execute(select(Interaction))).first() is None
        suppressions = (await session.execute(select(GmailSuppression))).scalars().all()
        kinds = {(s.gmail_message_id, s.gmail_thread_id) for s in suppressions}
        assert ("msg-r", None) in kinds and (None, "thr-9") in kinds

    # Re-polling the same message — and a follow-up in the suppressed thread — logs nothing.
    stub2 = _StubGmail(
        history=["msg-r", "msg-r2"],
        messages={
            "msg-r": _message("msg-r", sender="klant@client.nl", thread="thr-9"),
            "msg-r2": _message(
                "msg-r2", sender="klant@client.nl", thread="thr-9", rfc822="<r2@mail>"
            ),
        },
        history_id="9400",
    )
    assert await _poll(t, connection_id, stub2, monkeypatch) == 0


async def test_thread_inheritance_copies_mappings(client_for, monkeypatch) -> None:
    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)
    t = await make_tenant("gmail-thread")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Website", "company_id": company["id"]},
                headers=headers,
            )
        ).json()

        stub = _StubGmail(
            history=["msg-t1"],
            messages={"msg-t1": _message("msg-t1", sender="klant@client.nl", thread="thr-x")},
            history_id="9500",
        )
        assert await _poll(t, connection_id, stub, monkeypatch) == 1
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            first = (await session.execute(select(Interaction))).scalar_one()
            first_id = str(first.id)

        # The owner approves and maps the email to a project.
        assert (
            await c.post(f"/api/v1/interactions/{first_id}/approve", headers=headers)
        ).status_code == 200
        assert (
            await c.post(
                f"/api/v1/interactions/{first_id}/remap",
                json={"project_id": project["id"]},
                headers=headers,
            )
        ).status_code == 200

    # The follow-up in the same thread inherits the project mapping.
    stub2 = _StubGmail(
        history=["msg-t2"],
        messages={
            "msg-t2": _message(
                "msg-t2", sender="klant@client.nl", thread="thr-x", rfc822="<t2@mail>"
            )
        },
        history_id="9600",
    )
    assert await _poll(t, connection_id, stub2, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (await session.execute(select(Interaction).order_by(Interaction.created_at)))
            .scalars()
            .all()
        )
        follow_up = rows[-1]
        assert follow_up.gmail_message_id == "msg-t2"
        assert str(follow_up.project_id) == project["id"]
        assert follow_up.status == "pending"  # inherit_pending: mapped, still reviewed


async def test_approval_fetches_body_via_worker_path(client_for, monkeypatch) -> None:
    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)
    t = await make_tenant("gmail-body")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
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
        stub = _StubGmail(
            history=["msg-f"],
            messages={
                "msg-f": _message(
                    "msg-f", sender="klant@client.nl", body_text="De volledige inhoud."
                )
            },
            history_id="9700",
        )
        assert await _poll(t, connection_id, stub, monkeypatch) == 1
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row = (await session.execute(select(Interaction))).scalar_one()
            row_id = row.id

        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=headers)
        ).status_code == 200

    # The worker (or the sweep) fetches the body after approval.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await fetch_body(session, t.org, row_id) is True
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.status == "logged" and row.body_text == "De volledige inhoud."
        assert stub.full_fetches == ["msg-f"]


async def test_approval_stores_attachments_once(client_for, monkeypatch, tmp_path) -> None:
    """#180: the approval-time full fetch also saves the message's attachments into the
    storage backend, entity-linked to the interaction — idempotently (a sweep re-run must
    not duplicate them), skipping disallowed types, and never before approval (this path
    only runs on approved rows, so a rejected pending email leaves no stored bytes)."""
    from app.config import settings
    from app.core.storage.models import StoredFile

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)
    t = await make_tenant("gmail-attach")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/contacts",
            json={"first_name": "Klant", "email": "klant@client.nl"},
            headers=headers,
        )
        message = _message("msg-a", sender="klant@client.nl")
        message["payload"] = {
            "headers": message["payload"]["headers"],
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"De inhoud.").decode()},
                },
                {
                    "filename": "offerte.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-1", "size": 9},
                },
                {
                    "filename": "virus.exe",
                    "mimeType": "application/x-msdownload",
                    "body": {"attachmentId": "att-2", "size": 9},
                },
            ],
        }
        stub = _StubGmail(history=["msg-a"], messages={"msg-a": message}, history_id="9800")
        # The stub routes by last URL segment, so attachment ids resolve like message ids.
        stub.messages["att-1"] = {
            "size": 9,
            "data": base64.urlsafe_b64encode(b"%PDF-fake").decode(),
        }
        stub.messages["att-2"] = {
            "size": 9,
            "data": base64.urlsafe_b64encode(b"MZ-nope..").decode(),
        }
        assert await _poll(t, connection_id, stub, monkeypatch) == 1
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row_id = (await session.execute(select(Interaction))).scalar_one().id

        # Pending: nothing stored yet — reject must be able to leave no bytes anywhere.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert (await session.execute(select(StoredFile))).scalars().all() == []

        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=headers)
        ).status_code == 200

        # The worker path fetches body + attachments; run it twice to prove idempotency.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert await fetch_body(session, t.org, row_id) is True
            await session.commit()
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await fetch_body(session, t.org, row_id)
            await session.commit()

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            stored = (await session.execute(select(StoredFile))).scalars().all()
            # The .exe was skipped by the type allowlist; the PDF stored exactly once.
            assert [(f.filename, f.entity_type, f.entity_id) for f in stored] == [
                ("offerte.pdf", "interaction", row_id)
            ]
            assert stored[0].size_bytes == len(b"%PDF-fake")

        # Team-visible where the interaction is: the files endpoint lists it...
        listed = (
            await c.get(
                "/api/v1/files",
                params={"entity_type": "interaction", "entity_id": str(row_id)},
                headers=headers,
            )
        ).json()
        assert [f["filename"] for f in listed] == ["offerte.pdf"]

    # ...and never across tenants (the row is org-scoped like everything else).
    other = await make_tenant("gmail-attach-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as cb:
        assert (
            await cb.get(
                "/api/v1/files",
                params={"entity_type": "interaction", "entity_id": str(row_id)},
                headers=other_headers,
            )
        ).json() == []
