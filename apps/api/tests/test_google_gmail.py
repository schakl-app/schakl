"""google.gmail (#22): matching units, the poll pipeline, approval wiring, suppressions."""

from __future__ import annotations

import base64
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.crypto import encrypt
from app.db import async_session_maker, set_current_org
from app.integrations.google.gmail import matching
from app.integrations.google.gmail.gates import SkipReason
from app.integrations.google.gmail.models import GmailSkip, GmailSuppression
from app.integrations.google.gmail.service import (
    SKIP_RETENTION_DAYS,
    fetch_body,
    poll_connection,
    reap_skips,
)
from app.integrations.google.models import GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_GMAIL
from app.modules.interactions.models import Interaction
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant

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
    # A colleague Bcc'd on our own outgoing mail holds an INBOX copy with no SENT label.
    # Reading the label alone files it as though the client had written to us.
    assert matching.direction_of(["INBOX"], sender_internal=True) == "outbound"

    assert not matching.is_relevant(["DRAFT"], None)
    assert not matching.is_relevant(["INBOX", "Label_7"], "Label_7")  # the opt-out label
    assert matching.is_relevant(["INBOX"], "Label_7")

    # Colleague-to-colleague chatter is not a client touchpoint.
    members = {"me@agency.nl", "collega@agency.nl"}
    internal = [{"email": "me@agency.nl"}, {"email": "collega@agency.nl"}]
    assert matching.internal_only(internal, members)
    assert not matching.internal_only(participants, members)


def test_intended_owner_reads_the_headers_not_the_mailbox() -> None:
    """Whose email is this? Never "whoever's poll ran first"."""
    ours = {"luka@agency.nl", "info@agency.nl", "jan@agency.nl"}

    # Outgoing: the sender owns it, however many colleagues were copied in.
    sent = matching.parse_participants(
        {"From": "Luka <luka@agency.nl>", "To": "klant@client.nl", "Cc": "info@agency.nl"}
    )
    assert matching.intended_owner(sent, ours) == "luka@agency.nl"

    # Incoming: the first colleague in To, in header order — not the Cc'd shared mailbox,
    # and not whichever of them happens to be listed first in `ours`.
    received = matching.parse_participants(
        {"From": "klant@client.nl", "To": "jan@agency.nl, info@agency.nl"}
    )
    assert matching.intended_owner(received, ours) == "jan@agency.nl"

    cc_only = matching.parse_participants(
        {"From": "klant@client.nl", "To": "ander@client.nl", "Cc": "info@agency.nl"}
    )
    assert matching.intended_owner(cc_only, ours) == "info@agency.nl"

    # A Bcc'd copy names no colleague at all — Bcc is on nobody else's headers, which is
    # exactly why such a copy must not claim the email.
    bcc_only = matching.parse_participants(
        {"From": "Luka <luka@agency.nl>", "To": "klant@client.nl"}
    )
    assert matching.intended_owner(bcc_only, {"info@agency.nl"}) is None
    assert matching.intended_owner([], ours) is None


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


def test_mapping_ranks_the_client_above_the_agency() -> None:
    """#305: the agency is a company in its own list, so it matched like a client did.

    Its own record is the older one — created at setup, long before this week's customer — so
    "oldest link first" handed *every* email to the agency itself, and the reviewer remapped
    each one by hand. A colleague in Cc is not what the mail is about.
    """
    colleague, house, client = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    internal = frozenset({ours})

    # Oldest first, as the query returns them: staff, then our own office address, then the
    # customer who actually sent the thing.
    matches = [
        matching.ContactMatch(colleague, [ours], role="cc", is_staff=True),
        matching.ContactMatch(house, [ours], role="to"),
        matching.ContactMatch(client, [theirs], role="from"),
    ]
    resolved = matching.resolve_mappings(matches, internal_company_ids=internal)
    assert resolved == {"contact_id": client, "company_id": theirs}

    # Role breaks the tie between two outsiders: the sender outranks the Cc.
    other = uuid.uuid4()
    cc_first = [
        matching.ContactMatch(other, [ours], role="cc", is_staff=True),
        matching.ContactMatch(house, [theirs], role="cc"),
        matching.ContactMatch(client, [theirs], role="from"),
    ]
    assert matching.resolve_mappings(cc_first, internal_company_ids=internal)[
        "contact_id"
    ] == client

    # Ranked, never filtered: internal-only mail (gmail_log_internal) has nowhere else to go.
    only_us = [matching.ContactMatch(colleague, [ours], role="from", is_staff=True)]
    assert matching.resolve_mappings(only_us, internal_company_ids=internal) == {
        "contact_id": colleague,
        "company_id": ours,
    }

    # A colleague listed on a client's company too: the company list is ranked, not just the
    # contacts that produced it.
    both = [matching.ContactMatch(colleague, [ours, theirs], role="from", is_staff=True)]
    assert (
        matching.resolve_mappings(both, internal_company_ids=internal)["company_id"] == theirs
    )

    # A colleague carries the staff flag on their own, so a thread with one on it ranks right
    # even before the company set is derived. Knowing the *company* is what covers the people
    # on it who hold no login — ``office@``, ``administratie@`` — and it outranks the header:
    # our own address sending, with the customer in Cc, is still the customer's thread.
    house_only = [
        matching.ContactMatch(house, [ours], role="from"),
        matching.ContactMatch(client, [theirs], role="cc"),
    ]
    assert matching.resolve_mappings(house_only)["company_id"] == ours
    assert (
        matching.resolve_mappings(house_only, internal_company_ids=internal)["company_id"]
        == theirs
    )


def test_the_gate_asks_for_an_outsider_not_merely_a_match() -> None:
    """#324: "matched a contact" was never "involves somebody outside the agency".

    Staff are contacts on the agency's own company — the ordinary setup, and the very one
    ``internal_company_ids`` is derived from — so a newsletter addressed to one colleague
    matched *that colleague* and walked through the gate meant to stop it. The predicate the
    ranking already used is now named and shared, so the gate and the ranking cannot disagree.
    """
    colleague, house, client, stranger = (uuid.uuid4() for _ in range(4))
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    internal = frozenset({ours})

    staff = matching.ContactMatch(colleague, [ours], role="to", is_staff=True)
    office = matching.ContactMatch(house, [ours], role="to")  # administratie@, holds no login
    customer = matching.ContactMatch(client, [theirs], role="from")
    unattached = matching.ContactMatch(stranger, [], role="from")

    assert matching.is_internal_match(staff, internal)
    assert matching.is_internal_match(office, internal)
    assert not matching.is_internal_match(customer, internal)
    # A contact on no company at all is an outsider — an unattached prospect is exactly the
    # record this feed exists to fill in, and reading it as one of ours would lose it.
    assert not matching.is_internal_match(unattached, internal)
    # A colleague carries the flag on their own; the office address needs the derived company
    # set, which is the whole reason that set exists.
    assert matching.is_internal_match(staff)
    assert not matching.is_internal_match(office)

    # The gate. The newsletter's only match is the colleague it was addressed to.
    assert not matching.has_external_match([staff], internal)
    assert not matching.has_external_match([staff, office], internal)
    assert not matching.has_external_match([], internal)
    # A real customer on the same thread is untouched by any of this (#305, unchanged).
    assert matching.has_external_match([staff, office, customer], internal)
    assert matching.has_external_match([unattached], internal)


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
    cc: str | None = None,
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
    if cc:
        headers.append({"name": "Cc", "value": cc})
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
    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _stub_acting_as(stub))
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


async def test_a_matched_email_lands_on_the_contact_roster(client_for, monkeypatch) -> None:
    """#300: the gmail seam writes the roster, not only the lead column.

    ``record_email`` is the one write path outside the service, and every read now answers from
    ``interaction_contacts`` — so a seam that set ``contact_id`` alone would log the message
    against a person the API then reports as nobody, and drop it out of that contact's own
    timeline and panel counter with the column still holding their id. Asserted through the
    HTTP read and the ``?contact_id=`` filter, because that is where the loss would show.
    """
    t = await make_tenant("gmail-roster")
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

        stub = _StubGmail(
            history=["msg-1"],
            messages={"msg-1": _message("msg-1", sender="Klant <klant@client.nl>")},
            history_id="9100",
        )
        assert await _poll(t, connection_id, stub, monkeypatch) == 1

        listed = (await c.get("/api/v1/interactions", headers=headers)).json()["items"]
        assert len(listed) == 1
        assert [p["id"] for p in listed[0]["contacts"]] == [contact["id"]]
        # The lead pair still answers, because the roster is where it is derived from.
        assert listed[0]["contact_id"] == contact["id"]
        assert listed[0]["contact_name"] == "Klant"

        # …and the message is on that person's own timeline, which is the read that would
        # have silently gone empty.
        filtered = (
            await c.get(f"/api/v1/interactions?contact_id={contact['id']}", headers=headers)
        ).json()
        assert [row["id"] for row in filtered["items"]] == [listed[0]["id"]]


async def test_poll_files_under_the_client_not_the_agency(client_for, monkeypatch) -> None:
    """#305: an email in review defaulted to the agency's own company, every time.

    An agency keeps itself in its own company list — that is where its own domains, hosting
    and invoices hang — with its staff and its ``administratie@`` address as contacts on it.
    Those records date from setup, so on any thread with a colleague in Cc they matched
    *first*, and "oldest link first" filed the mail under the agency instead of the customer
    who sent it. Seeded in exactly that order, because the order is the bug.
    """
    t = await make_tenant("gmail-house")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        house = (
            await c.post("/api/v1/companies", json={"name": "Bureau zelf"}, headers=headers)
        ).json()
        # A colleague (a real member) and the office address, both contacts on our own company.
        for first_name, email in (("Collega", t.user.email), ("Office", "office@agency.nl")):
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": first_name,
                    "email": email,
                    "company_ids": [house["id"]],
                },
                headers=headers,
            )
        client_co = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        klant = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Klant",
                    "email": "klant@client.nl",
                    "company_ids": [client_co["id"]],
                },
                headers=headers,
            )
        ).json()

    stub = _StubGmail(
        history=["msg-1"],
        messages={
            "msg-1": _message(
                "msg-1",
                sender="Klant <klant@client.nl>",
                to=t.user.email,
                cc="office@agency.nl",
            )
        },
        history_id="9200",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.company_id == uuid.UUID(client_co["id"])
        assert row.contact_id == uuid.UUID(klant["id"])


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
            await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
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


async def test_a_directly_invited_client_is_not_a_colleague(client_for, monkeypatch) -> None:
    """The other half of "external login" (#274): the seeded ``client`` role, no contact link.

    ``portal_user_ids`` answers "is this user contact-linked?", so a client invited from
    Instellingen → Gebruikers rather than from their contact's portal section came back as
    staff: their address landed in ``member_emails``, every mail they wrote to a colleague read
    as colleague-to-colleague chatter, and the poll dropped it. Silently — no pending row, no
    notification, nothing in the log.

    The second-order damage is the one that made it a whole client going dark rather than one
    person: ``company_ids`` is derived from ``member_emails``, so *their own company* read as
    the agency's own, and since #324's gate every other contact there was dropped with them.
    """
    t = await make_tenant("gmail-client-role")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        for first_name, email in (("Klant", "klant@client.nl"), ("Collega", "co@client.nl")):
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": first_name,
                    "email": email,
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        # Invited straight into the client role — no ``contacts.user_id`` link is created.
        assert (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "klant@client.nl", "full_name": "Klant", "role": "client"},
                headers=headers,
            )
        ).status_code == 201

    stub = _StubGmail(
        history=["msg-client", "msg-colleague"],
        messages={
            "msg-client": _message(
                "msg-client", sender="Klant <klant@client.nl>", to=t.user.email
            ),
            # Nobody on this one holds a login at all: it is the company that must not have
            # been reclassified, not just the one address.
            "msg-colleague": _message(
                "msg-colleague",
                sender="Collega <co@client.nl>",
                to=t.user.email,
                thread="thr-2",
            ),
        },
        history_id="9300",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 2

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(Interaction))).scalars().all()
        assert [row.status for row in rows] == ["pending", "pending"]
        assert {row.company_id for row in rows} == {uuid.UUID(company["id"])}


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


async def test_a_staff_contact_does_not_open_the_gate(client_for, monkeypatch) -> None:
    """#324: the whole mailbox was landing in the review queue, one rejection at a time.

    The gate reads as though it cannot happen — *"External mail still needs a known contact"* —
    and the code asked a different question: had *anything* matched. An agency puts its own
    people in its own contact list (that is where ``_internals`` derives its own companies
    from), so a newsletter to one colleague matched the colleague, filed itself on the agency's
    own company as pending, and notified its owner. Every supplier invoice, cold email, GitHub
    notification and password reset with it.

    Seeded as the setup that reproduces it — staff *and* the login-less ``office@`` address as
    contacts on the agency's own record — and polled with the four shapes at once, because the
    bug is not that any one of them logs but that the gate cannot tell them apart.
    """
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-staff-gate")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    collega = "collega@gmail-staff-gate-example.nl"
    async with client_for(t.host) as c:
        await _member(c, headers, collega)
        house = (
            await c.post("/api/v1/companies", json={"name": "Bureau zelf"}, headers=headers)
        ).json()
        for first_name, email in (
            ("Eigenaar", t.user.email),
            ("Collega", collega),
            ("Office", "office@agency.nl"),
        ):
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": first_name,
                    "email": email,
                    "company_ids": [house["id"]],
                },
                headers=headers,
            )
        client_co = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        klant = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Klant",
                    "email": "klant@client.nl",
                    "company_ids": [client_co["id"]],
                },
                headers=headers,
            )
        ).json()

    stub = _StubGmail(
        history=["msg-news", "msg-office", "msg-intern", "msg-klant"],
        messages={
            # A newsletter to a colleague: nobody outside is a contact, so nothing is.
            "msg-news": _message(
                "msg-news",
                sender="Nieuwsbrief <nieuws@example.com>",
                to=t.user.email,
                thread="thr-news",
            ),
            # The same, addressed to the office address — internal by *company*, not by
            # ``is_staff``, which is the half of the predicate a staff-only check would miss.
            "msg-office": _message(
                "msg-office",
                sender="Leverancier <factuur@leverancier.nl>",
                to="office@agency.nl",
                thread="thr-office",
            ),
            # Colleague to colleague, with both of them contacts: still internal, and
            # ``gmail_log_internal`` is off.
            "msg-intern": _message(
                "msg-intern", sender=f"Collega <{collega}>", to=t.user.email, thread="thr-int"
            ),
            # The one that is actually a client touchpoint.
            "msg-klant": _message(
                "msg-klant",
                sender="Klant <klant@client.nl>",
                to=t.user.email,
                thread="thr-klant",
            ),
        },
        history_id="9910",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.gmail_message_id == "msg-klant"
        # …and #305's answer is unchanged: the colleague on the thread does not steal it.
        assert row.company_id == uuid.UUID(client_co["id"])
        assert row.contact_id == uuid.UUID(klant["id"])

        from app.modules.notifications.models import NotificationEvent

        pending = (
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
        assert len(pending) == 1  # one review to do, not four

        # Opting in is the *only* door a message with nobody outside on it has — and opening
        # it must not reopen the newsletter's.
        settings_row = (await session.execute(select(GoogleSettings))).scalar_one()
        settings_row.gmail_log_internal = True
        await session.commit()

    stub2 = _StubGmail(
        history=["msg-news2", "msg-intern2"],
        messages={
            "msg-news2": _message(
                "msg-news2",
                sender="Nieuwsbrief <nieuws@example.com>",
                to=t.user.email,
                thread="thr-news2",
                rfc822="<news2@mail>",
            ),
            "msg-intern2": _message(
                "msg-intern2",
                sender=f"Collega <{collega}>",
                to=t.user.email,
                thread="thr-int2",
                rfc822="<intern2@mail>",
            ),
        },
        history_id="9920",
    )
    assert await _poll(t, connection_id, stub2, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        logged = (
            (await session.execute(select(Interaction).order_by(Interaction.created_at)))
            .scalars()
            .all()
        )
        assert [row.gmail_message_id for row in logged] == ["msg-klant", "msg-intern2"]


async def _colleague_mailbox(t, email: str, *, syncing: bool) -> uuid.UUID:
    """A second member with their own Google grant, syncing or not."""
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = GoogleConnection(
            org_id=t.org.id,
            user_id=(await _user_id_for(session, email)),
            google_sub=f"sub-{email}",
            email=email,
            scopes=["openid", "email", SCOPE_GMAIL] if syncing else ["openid", "email"],
            refresh_token_encrypted=encrypt("rt"),
            gmail_sync_enabled=syncing,
            gmail_history_id="5",
        )
        session.add(connection)
        await session.commit()
        return connection.id


async def _user_id_for(session, email: str) -> uuid.UUID:
    from sqlalchemy import text

    return (
        await session.execute(
            text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email.lower()}
        )
    ).scalar_one()


async def _client_contact(c, headers, host_suffix: str) -> dict:
    company = (
        await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
    ).json()
    await c.post(
        "/api/v1/contacts",
        json={
            "first_name": "Klant",
            "last_name": "Persoon",
            "email": f"klant@{host_suffix}",
            "company_ids": [company["id"]],
        },
        headers=headers,
    )
    return company


async def test_a_bcc_copy_defers_to_the_senders_own_mailbox(client_for, monkeypatch) -> None:
    """The bug this fixes: a shared mailbox Bcc'd on everything claimed every email.

    One email, one row (the RFC-822 dedup), so the owner used to be whichever mailbox polled
    first. ``info@`` won, the row read *inbound*, and — a pending row being private to its
    owner with no admin escape — the colleague who actually wrote the mail could not see it
    anywhere. The Bcc'd copy now stands aside so the sender's own copy logs it.
    """
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-bcc-defer")  # the owner's mailbox is the shared info@ one
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _member(c, headers, "luka@gmail-bcc-defer-example.nl")
        await _client_contact(c, headers, "client.nl")
    await _colleague_mailbox(t, "luka@gmail-bcc-defer-example.nl", syncing=True)

    # info@'s copy of Luka's outgoing mail: an ordinary INBOX message naming only Luka and
    # the client, because Bcc is on nobody's headers but the sender's own.
    stub = _StubGmail(
        history=["msg-bcc"],
        messages={
            "msg-bcc": _message(
                "msg-bcc",
                sender="Luka <luka@gmail-bcc-defer-example.nl>",
                to="klant@client.nl",
            )
        },
        history_id="9500",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.execute(select(Interaction))).all() == []


async def test_the_senders_own_copy_is_never_deferred(client_for, monkeypatch) -> None:
    """A SENT copy is the sender's by definition — the deferral must never give it away,
    or the one mailbox that should log an email would be the one that refuses to."""
    t = await make_tenant("gmail-sent-keeps")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _client_contact(c, headers, "client.nl")

    stub = _StubGmail(
        history=["msg-sent"],
        messages={
            "msg-sent": _message(
                "msg-sent", sender=t.user.email, to="klant@client.nl", labels=["SENT"]
            )
        },
        history_id="9600",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.owner_user_id == t.user.id
        assert row.direction == "outbound"


async def test_a_copy_is_kept_when_the_owners_mailbox_will_never_poll(
    client_for, monkeypatch
) -> None:
    """Standing aside is only safe for a mailbox that actually polls.

    Luka holds a grant without the Gmail scope, so nothing of theirs is ever fetched. This
    copy is the only one there will ever be: it logs here rather than being deferred into
    oblivion — and it is *outbound*, read from the sender rather than from the SENT label
    that a colleague's copy does not carry.
    """
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-bcc-keep")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _member(c, headers, "luka@gmail-bcc-keep-example.nl")
        company = await _client_contact(c, headers, "client.nl")
    await _colleague_mailbox(t, "luka@gmail-bcc-keep-example.nl", syncing=False)

    stub = _StubGmail(
        history=["msg-bcc"],
        messages={
            "msg-bcc": _message(
                "msg-bcc",
                sender="Luka <luka@gmail-bcc-keep-example.nl>",
                to="klant@client.nl",
            )
        },
        history_id="9700",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.owner_user_id == t.user.id  # the only mailbox that has it
        assert row.direction == "outbound"
        assert row.company_id == uuid.UUID(company["id"])


async def test_an_incoming_mail_defers_to_its_first_named_recipient(
    client_for, monkeypatch
) -> None:
    """Incoming follows the same rule from the other end: To order is addressing order, so a
    mail *to* Luka with the shared mailbox in Cc is Luka's, not whoever's poll ran first."""
    from tests.test_notification_channels import _member

    t = await make_tenant("gmail-to-defer")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _member(c, headers, "luka@gmail-to-defer-example.nl")
        await _client_contact(c, headers, "client.nl")
    await _colleague_mailbox(t, "luka@gmail-to-defer-example.nl", syncing=True)

    stub = _StubGmail(
        history=["msg-in"],
        messages={
            "msg-in": _message(
                "msg-in",
                sender="klant@client.nl",
                to="luka@gmail-to-defer-example.nl",
                cc=t.user.email,
            )
        },
        history_id="9800",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0


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


async def test_approval_keeps_the_html_formatting_and_inlines_the_logo(
    client_for, monkeypatch, tmp_path
) -> None:
    """A synced e-mail and an uploaded ``.eml`` must read the same, so the gmail feed converts
    its HTML part too — and the signature logo the body points at becomes content of that body
    rather than an attachment chip on every message the sender ever sent."""
    from app.config import settings
    from app.core.storage.models import StoredFile

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    async def _quiet_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _quiet_enqueue)
    t = await make_tenant("gmail-rich")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    html = (
        "<html><body><p>Beste Stan,</p><ul><li>Hosting</li><li>SSL</li></ul>"
        '<p><img src="cid:logo@bureau" alt="Bureau"></p></body></html>'
    )
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/contacts",
            json={"first_name": "Klant", "email": "klant@client.nl"},
            headers=headers,
        )
        message = _message("msg-r", sender="klant@client.nl")
        message["payload"] = {
            "headers": message["payload"]["headers"],
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Beste Stan, Hosting SSL").decode()},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
                },
                {
                    # No filename at all — the way a related image often arrives. Before, that
                    # was the reason it went unstored; now the body's own reference decides.
                    "mimeType": "image/gif",
                    "headers": [{"name": "Content-ID", "value": "<logo@bureau>"}],
                    "body": {"attachmentId": "att-logo", "size": 6},
                },
            ],
        }
        stub = _StubGmail(history=["msg-r"], messages={"msg-r": message}, history_id="9900")
        stub.messages["att-logo"] = {
            "size": 6,
            "data": base64.urlsafe_b64encode(b"GIF89a").decode(),
        }
        assert await _poll(t, connection_id, stub, monkeypatch) == 1
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row_id = (await session.execute(select(Interaction))).scalar_one().id
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=headers)
        ).status_code == 200
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            assert await fetch_body(session, t.org, row_id) is True
            await session.commit()

        detail = (await c.get(f"/api/v1/interactions/{row_id}", headers=headers)).json()
        # The plain part is still what search reads; the formatting rides beside it.
        assert detail["body_text"] == "Beste Stan, Hosting SSL"
        assert detail["body_markdown"] is not None
        assert "- Hosting" in detail["body_markdown"]
        assert "](file:" in detail["body_markdown"]

        # The logo is inline: it draws in the body and is absent from the attachment list.
        listed = (
            await c.get(
                "/api/v1/files",
                params={"entity_type": "interaction", "entity_id": str(row_id)},
                headers=headers,
            )
        ).json()
        assert listed == []

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        stored = (await session.execute(select(StoredFile))).scalars().all()
        assert [(f.content_id, f.size_bytes) for f in stored] == [("logo@bureau", 6)]


# --------------------------------------------------------------------------- #
# The manual "scan my mailbox now" button (#341)
# --------------------------------------------------------------------------- #


async def test_manual_refresh_polls_once_and_then_cools_down(client_for, monkeypatch) -> None:
    """One press logs the mail; the next press inside the minute is refused, not re-polled.

    The cooldown is the whole rate limit, so the assertion that matters is not the status
    string but ``stub.calls``: a second poll that quietly happened while the response said
    "cooldown" would spend Gmail quota with nothing on the screen to show for it.
    """
    t = await make_tenant("gmail-refresh")
    await _seed(t)
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
            history=["msg-1"],
            messages={"msg-1": _message("msg-1", sender="Klant <klant@client.nl>")},
            history_id="9100",
        )
        polls = []
        factory = _stub_acting_as(stub)

        @asynccontextmanager
        async def _counting(session, org, connection):  # noqa: ANN001
            polls.append(connection.id)
            async with factory(session, org, connection) as inner:
                yield inner

        monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _counting)

        first = await c.post("/api/v1/google/gmail/refresh", headers=headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "polled" and body["logged"] == 1
        assert body["sync"]["available"] is True
        assert body["sync"]["last_polled_at"] is not None
        assert body["sync"]["retry_after_seconds"] > 0  # the press it just spent

        second = await c.post("/api/v1/google/gmail/refresh", headers=headers)
        assert second.status_code == 200, second.text
        cooled = second.json()
        assert cooled["status"] == "cooldown" and cooled["logged"] == 0
        assert 0 < cooled["sync"]["retry_after_seconds"] <= 60
        # Still one poll: the refusal never reached Gmail.
        assert len(polls) == 1

        # And the status read agrees with the refresh's own answer.
        status = (await c.get("/api/v1/google/gmail/status", headers=headers)).json()
        assert status["available"] is True
        assert status["last_polled_at"] == body["sync"]["last_polled_at"]
        assert status["retry_after_seconds"] > 0

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        assert row.kind == "email" and row.status == "pending"


async def test_manual_refresh_is_refused_when_the_mailbox_is_not_syncing(
    client_for,
) -> None:
    """A control that would always refuse is never drawn — and the API says why it would."""
    t = await make_tenant("gmail-refresh-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Nothing connected at all: the org has not even switched Gmail on.
        assert (await c.post("/api/v1/google/gmail/refresh", headers=headers)).status_code == 409
        status = (await c.get("/api/v1/google/gmail/status", headers=headers)).json()
        assert status == {
            "connected": False,
            "gmail_enabled": False,
            "sync_enabled": False,
            "scope_granted": False,
            "connection_error": False,
            "last_polled_at": None,
            "available": False,
            "retry_after_seconds": 0,
        }

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(GoogleSettings(org_id=t.org.id, gmail_enabled=True))
        session.add(
            GoogleConnection(
                org_id=t.org.id,
                user_id=t.user.id,
                google_sub="sub",
                email="me@agency.nl",
                scopes=["openid", "email", SCOPE_GMAIL],
                refresh_token_encrypted=encrypt("rt"),
                gmail_sync_enabled=False,  # connected, mailbox opted out
            )
        )
        await session.commit()

    async with client_for(t.host) as c:
        refused = await c.post("/api/v1/google/gmail/refresh", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.gmail_sync_off"
        status = (await c.get("/api/v1/google/gmail/status", headers=headers)).json()
        assert status["connected"] is True and status["available"] is False


async def test_manual_refresh_spends_its_budget_even_when_gmail_fails(
    client_for, monkeypatch
) -> None:
    """A failing mailbox must not become a control anyone can hold down.

    The stamp is written before the poll and outside its savepoint precisely so this holds:
    the poll rolls back, the cooldown does not.
    """
    t = await make_tenant("gmail-refresh-fail")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)

    @asynccontextmanager
    async def _broken(session, org, connection):  # noqa: ANN001, ARG001
        raise RuntimeError("gmail is having a day")
        yield  # pragma: no cover

    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _broken)

    async with client_for(t.host) as c:
        first = await c.post("/api/v1/google/gmail/refresh", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "error"
        # The failure is reported, the budget is spent.
        second = await c.post("/api/v1/google/gmail/refresh", headers=headers)
        assert second.json()["status"] == "cooldown"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        assert connection.gmail_manual_poll_at is not None
        assert connection.gmail_last_polled_at is None  # the poll itself never landed


# --------------------------------------------------------------------------- #
# Pulling a message in by hand (#342)
# --------------------------------------------------------------------------- #


def test_parse_reference_accepts_the_three_things_that_actually_resolve() -> None:
    """Hex ids, ``msg-f:`` decimals and Message-IDs — and a named refusal for the rest.

    The last case is the one worth pinning: what a person copies out of Gmail's address bar
    today is an opaque ``FMfcgz…`` web id the API cannot read *or* convert, so answering it
    with the generic "unreadable" error would send them off to re-copy a link that will never
    work. It gets its own key, which is what lets the screen name the two things that do.
    """
    from app.errors import AppError
    from app.integrations.google.gmail import manual

    assert manual.parse_reference("18c2d3e4f5a6b7c8") == manual.Reference(
        kind="id", value="18c2d3e4f5a6b7c8"
    )
    assert manual.parse_reference("  18C2D3E4F5A6B7C8 ").value == "18c2d3e4f5a6b7c8"
    # Same id, other base — Gmail's own links mix the two.
    assert manual.parse_reference("msg-f:1785443000000000000").kind == "id"
    assert manual.parse_reference("msg-f:1785443000000000000").value == format(
        1785443000000000000, "x"
    )
    assert manual.parse_reference("<CAF=abc123@mail.gmail.com>") == manual.Reference(
        kind="rfc822", value="CAF=abc123@mail.gmail.com"
    )
    assert manual.parse_reference("CAF=abc123@mail.gmail.com").kind == "rfc822"
    # A URL carrying a usable id, in the fragment and in the older `th=` query.
    assert (
        manual.parse_reference(
            "https://mail.google.com/mail/u/0/#all/18c2d3e4f5a6b7c8"
        ).value
        == "18c2d3e4f5a6b7c8"
    )
    assert (
        manual.parse_reference(
            "https://mail.google.com/mail/u/0/#inbox/18c2d3e4f5a6b7c8/18c2d3e4f5a6b7ff"
        ).value
        == "18c2d3e4f5a6b7ff"  # the *last* segment: an opened message inside its thread
    )
    assert (
        manual.parse_reference("https://mail.google.com/mail/u/0/?th=18c2d3e4f5a6b7c8").value
        == "18c2d3e4f5a6b7c8"
    )

    for opaque in (
        "https://mail.google.com/mail/u/0/#inbox/FMfcgzGtxSrbLZwqPjRmVXbKlnTwWQqd",
        "https://mail.google.com/mail/u/0/#inbox",
    ):
        try:
            manual.parse_reference(opaque)
        except AppError as exc:
            assert exc.message_key == "errors.gmail_reference_web_id", opaque
        else:  # pragma: no cover
            raise AssertionError(f"{opaque} should not resolve")

    for junk in ("", "   ", "not a reference"):
        try:
            manual.parse_reference(junk)
        except AppError as exc:
            assert exc.message_key in {
                "errors.gmail_reference_unreadable",
            }, junk
        else:  # pragma: no cover
            raise AssertionError(f"{junk!r} should not resolve")


class _StubManualGmail:
    """Gmail for the manual paths: message/thread reads plus the one rfc822msgid lookup."""

    def __init__(
        self,
        *,
        messages: dict[str, dict],
        threads: dict[str, list[str]],
        labels: list[dict] | None = None,
    ) -> None:
        self.messages = messages
        self.threads = threads
        self.labels = labels or []
        self.calls: list[str] = []
        #: Every ``q`` this stub was asked, so a test can assert what the search *became*
        #: rather than only what it returned — the injection boundary is the query string.
        self.queries: list[str] = []

    async def get(self, url: str, **kwargs) -> _StubResponse:
        params = kwargs.get("params") or {}
        self.calls.append(url)
        if url.endswith("/labels"):
            return _StubResponse(200, {"labels": self.labels})
        if url.endswith("/messages") and "q" in params:
            query = str(params["q"])
            self.queries.append(query)
            if query.startswith("rfc822msgid:"):
                wanted = query.removeprefix("rfc822msgid:")
                hits = [
                    {"id": mid}
                    for mid, message in self.messages.items()
                    if any(
                        h["name"] == "Message-ID" and h["value"].strip("<>") == wanted
                        for h in message["payload"]["headers"]
                    )
                ]
                return _StubResponse(200, {"messages": hits})
            # A search: Gmail matches however it likes, so the stub answers on the one term
            # a test can control — an address anywhere in the headers.
            addresses = re.findall(r"[\w.+-]+@[\w.-]+", query)
            hits = [
                {"id": mid}
                for mid, message in self.messages.items()
                if any(
                    any(a in h["value"] for a in addresses)
                    for h in message["payload"]["headers"]
                    if h["name"] in ("From", "To", "Cc")
                )
            ]
            return _StubResponse(200, {"messages": hits})
        if "/threads/" in url:
            thread_id = url.rsplit("/", 1)[-1]
            ids = self.threads.get(thread_id)
            if ids is None:
                return _StubResponse(404)
            return _StubResponse(
                200, {"id": thread_id, "messages": [self.messages[i] for i in ids]}
            )
        message_id = url.rsplit("/", 1)[-1]
        message = self.messages.get(message_id)
        if message is None:
            return _StubResponse(404)
        return _StubResponse(200, message)


def _manual_stub() -> _StubManualGmail:
    first = _message(
        "msg-a",
        sender="Nieuwe Klant <nieuw@prospect.nl>",
        subject="Offerteaanvraag",
        thread="thr-9",
        rfc822="<aanvraag-1@prospect.nl>",
        body_text="Graag een offerte voor een nieuwe website.",
    )
    reply = _message(
        "msg-b",
        sender="me@agency.nl",
        to="nieuw@prospect.nl",
        subject="Re: Offerteaanvraag",
        thread="thr-9",
        labels=["SENT"],
        rfc822="<antwoord-1@agency.nl>",
    )
    return _StubManualGmail(
        messages={"msg-a": first, "msg-b": reply},
        threads={"thr-9": ["msg-a", "msg-b"]},
    )


async def test_manual_lookup_and_import_logs_a_message_the_poller_skipped(
    client_for, monkeypatch
) -> None:
    """The headline case: a prospect who is nobody's contact yet, logged by id.

    ``has_external_match`` drops exactly this message on every poll — correctly, since there is
    no contact to match — so the auto path can never produce this row. The import writes it
    from the real headers, files it where the caller said, and pulls the body in the same
    request (a person is waiting, and they already decided it belongs on the timeline).
    """
    t = await make_tenant("gmail-manual")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))
    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Prospect BV"}, headers=headers)
        ).json()

        found = await c.get(
            "/api/v1/google/gmail/lookup",
            params={"reference": "<aanvraag-1@prospect.nl>"},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        body = found.json()
        # One message named, the whole conversation shown (#372). It used to answer with just
        # that message when the reference resolved cleanly — so the better your reference, the
        # less you saw, and "which of these are missing?" could only be asked by accident.
        assert [m["message_id"] for m in body["messages"]] == ["msg-a", "msg-b"]
        assert body["widened_to_thread"] is True
        assert body["thread_id"] == "thr-9"
        candidate = body["messages"][0]
        assert candidate["subject"] == "Offerteaanvraag"
        assert candidate["from_email"] == "nieuw@prospect.nl"
        assert candidate["logged"] is False and candidate["interaction_id"] is None
        # And it says *why* it is not here, out of the ingest's own chain: a prospect nobody
        # has made a contact of yet is exactly what `has_external_match` declines.
        assert candidate["skip_reason"] == "no_external_match"

        created = await c.post(
            "/api/v1/google/gmail/import",
            json={"message_id": "msg-a", "company_id": company["id"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        result = created.json()
        assert result["subject"] == "Offerteaanvraag"

        detail = (
            await c.get(f"/api/v1/interactions/{result['interaction_id']}", headers=headers)
        ).json()
        assert detail["kind"] == "email"
        assert detail["status"] == "logged"  # never pending: somebody went and fetched it
        assert detail["source"] == "gmail"
        assert detail["company_id"] == company["id"]
        assert detail["deep_link"] and "msg-a" in detail["deep_link"]

        # And now the lookup says so, with the row to open instead of a button that would 409.
        again = (
            await c.get(
                "/api/v1/google/gmail/lookup",
                params={"reference": "<aanvraag-1@prospect.nl>"},
                headers=headers,
            )
        ).json()
        assert again["messages"][0]["logged"] is True
        assert again["messages"][0]["interaction_id"] == result["interaction_id"]

        # The same message twice in one mailbox is never what anybody meant.
        repeat = await c.post(
            "/api/v1/google/gmail/import",
            json={"message_id": "msg-a", "company_id": company["id"]},
            headers=headers,
        )
        assert repeat.status_code == 409
        assert repeat.json()["error"]["message"] == "errors.interactions_gmail_already_logged"


async def test_thread_gap_fill_marks_what_is_already_on_the_timeline(
    client_for, monkeypatch
) -> None:
    """Tier 2: the conversation we already hold the id for, with the gaps named.

    No search and no new reach — the thread id came off a row the poller wrote. What the screen
    needs is which of its messages are missing, so that is what the read answers.
    """
    t = await make_tenant("gmail-thread-gap")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))
    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        listed = await c.get("/api/v1/google/gmail/threads/thr-9", headers=headers)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["thread_id"] == "thr-9"
        assert [m["message_id"] for m in body["messages"]] == ["msg-a", "msg-b"]
        assert [m["logged"] for m in body["messages"]] == [False, False]
        # Our own reply reads as outbound off its SENT label, the way the feed reads it.
        assert body["messages"][1]["direction"] == "outbound"

        await c.post(
            "/api/v1/google/gmail/import", json={"message_id": "msg-a"}, headers=headers
        )
        after = (
            await c.get("/api/v1/google/gmail/threads/thr-9", headers=headers)
        ).json()
        assert [m["logged"] for m in after["messages"]] == [True, False]

        missing = await c.get("/api/v1/google/gmail/threads/thr-nope", headers=headers)
        assert missing.status_code == 404


async def test_manual_gmail_refuses_a_mailbox_that_is_not_ours_to_read(client_for) -> None:
    """Every gate answers before Gmail is called at all — and none of them is a 500."""
    t = await make_tenant("gmail-manual-gate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        blocked = await c.get(
            "/api/v1/google/gmail/lookup",
            params={"reference": "18c2d3e4f5a6b7c8"},
            headers=headers,
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["message"] == "errors.gmail_disabled"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(GoogleSettings(org_id=t.org.id, gmail_enabled=True))
        session.add(
            GoogleConnection(
                org_id=t.org.id,
                user_id=t.user.id,
                google_sub="sub",
                email="me@agency.nl",
                scopes=["openid", "email"],  # connected, but never granted Gmail
                refresh_token_encrypted=encrypt("rt"),
            )
        )
        await session.commit()

    async with client_for(t.host) as c:
        refused = await c.get(
            "/api/v1/google/gmail/lookup",
            params={"reference": "18c2d3e4f5a6b7c8"},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.gmail_sync_off"


async def test_manual_import_reaches_the_ai_task_fill_in(client_for, monkeypatch) -> None:
    """#342's point: the enrichment offer hangs off filing onto a task, not off review.

    And it is made **after the body has landed**, the order the ``.eml`` upload keeps: the
    offer enqueues the worker with a fixed head start, so an offer made before the Google
    round trips let a mail with a few attachments outrun it — the job found no ``queued`` row
    to claim and the task sat on "in de wachtrij" until the reaper failed it.
    """
    from app.modules.interactions import system as interactions_system

    t = await make_tenant("gmail-manual-enrich")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))
    monkeypatch.setattr("app.integrations.google.gmail.service.acting_as", _stub_acting_as(stub))

    offered: list[tuple[str, str]] = []
    body_at_offer: list[str | None] = []

    async def _capture(ctx, interaction_id, task_id):  # noqa: ANN001, ARG001
        offered.append((str(interaction_id), str(task_id)))
        return object()

    real_offer = interactions_system.offer_task_enrichment

    async def _offer(ctx, row):  # noqa: ANN001
        body_at_offer.append(row.body_text)
        await real_offer(ctx, row)

    monkeypatch.setattr("app.modules.interactions.jobs.schedule_enrichment", _capture)
    monkeypatch.setattr(
        "app.integrations.google.gmail.manual.interactions_system.offer_task_enrichment", _offer
    )
    monkeypatch.setattr(
        "app.modules.interactions.enrich.available",
        lambda ctx: _true(),  # noqa: ARG005
    )

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Prospect BV"}, headers=headers)
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Offerte maken",
                    "company_id": company["id"],
                },
                headers=headers,
            )
        ).json()
        created = await c.post(
            "/api/v1/google/gmail/import",
            json={"message_id": "msg-a", "task_id": task["id"], "enrich_task": True},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert offered == [(created.json()["interaction_id"], task["id"])]
        # The offer saw the body: it was made after the fetch, not before it.
        assert body_at_offer == ["Graag een offerte voor een nieuwe website."]


async def _true() -> bool:
    return True


# --------------------------------------------------------------------------- #
# The dry run: why is this email not on the timeline? (#372)
# --------------------------------------------------------------------------- #


async def test_the_explainer_and_the_ingest_are_the_same_function(
    client_for, monkeypatch
) -> None:
    """The point of the refactor, asserted as behaviour rather than as structure.

    A message the poller declines must be *reported* as declined for the same reason, and one
    it would accept must report no reason at all — without this test naming the gate. The only
    thing that changes between the two halves is a contact row, which opens
    ``has_external_match``, so the explainer's verdict has to flip with the ingest's. An
    explainer that had drifted would pass one half and fail the other.
    """
    t = await make_tenant("gmail-explain-same")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:

        async def _reason() -> str | None:
            body = (
                await c.get(
                    "/api/v1/google/gmail/lookup",
                    params={"reference": "<aanvraag-1@prospect.nl>"},
                    headers=headers,
                )
            ).json()
            return next(
                m for m in body["messages"] if m["message_id"] == "msg-a"
            )["skip_reason"]

        assert await _reason() == SkipReason.NO_EXTERNAL_MATCH.value

        company = (
            await c.post("/api/v1/companies", json={"name": "Prospect BV"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Nieuwe",
                "last_name": "Klant",
                "email": "nieuw@prospect.nl",
                "company_id": company["id"],
            },
            headers=headers,
        )
        # Same message, same mailbox, one contact row later: the gate that declined it is open,
        # so the explainer must stop naming it — and nothing here mentions the gate by name.
        assert await _reason() is None


async def test_a_reason_names_the_label_that_caused_it(client_for, monkeypatch) -> None:
    """"A label excluded it" is unactionable when you cannot see which label.

    The reason travels with the one string it needs and no more: the label's name, never the
    subject or the participants — ``skip_row_values``' rule, held to on the read side too.
    """
    t = await make_tenant("gmail-explain-label")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        connection.gmail_excluded_label = "geen-crm"
        await session.commit()

    stub = _StubManualGmail(
        messages={
            "msg-x": _message(
                "msg-x",
                sender="Klant <klant@client.nl>",
                subject="Nieuwsbrief",
                thread="thr-x",
                labels=["INBOX", "Label_7"],
                rfc822="<nieuws-1@client.nl>",
            )
        },
        threads={"thr-x": ["msg-x"]},
        labels=[{"id": "Label_7", "name": "geen-crm"}],
    )
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        body = (
            await c.get(
                "/api/v1/google/gmail/lookup",
                params={"reference": "<nieuws-1@client.nl>"},
                headers=headers,
            )
        ).json()
        message = body["messages"][0]
        assert message["skip_reason"] == SkipReason.EXCLUDED_LABEL.value
        assert message["skip_detail"] == {"label": "geen-crm"}
        assert "Nieuwsbrief" not in str(message["skip_detail"])


async def test_a_message_older_than_the_mailbox_says_it_was_never_offered(
    client_for, monkeypatch
) -> None:
    """The honest limit: the chain never ran for these, so no gate verdict is the truth.

    The first poll baselines and imports nothing — connecting a mailbox is opt-in going forward
    — so a message from before that day was never offered to any gate. Reporting one anyway
    would be a confident answer to a question nobody asked; the screen says "from before you
    connected this mailbox", and the response is simply to import it.
    """
    t = await make_tenant("gmail-explain-old")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        connection.created_at = datetime.now(UTC) - timedelta(days=30)
        await session.commit()

    old = _message(
        "msg-old",
        sender="Klant <klant@client.nl>",
        subject="Vorig jaar",
        thread="thr-old",
        rfc822="<oud-1@client.nl>",
    )
    old["internalDate"] = str(
        int((datetime.now(UTC) - timedelta(days=200)).timestamp() * 1000)
    )
    stub = _StubManualGmail(messages={"msg-old": old}, threads={"thr-old": ["msg-old"]})
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        body = (
            await c.get(
                "/api/v1/google/gmail/lookup",
                params={"reference": "<oud-1@client.nl>"},
                headers=headers,
            )
        ).json()
        message = body["messages"][0]
        assert message["before_connection"] is True
        # Never both: "it was never offered" and "we looked and saw it" cannot both be true.
        assert message["never_offered"] is False


# --------------------------------------------------------------------------- #
# Searching the caller's own mailbox (#372)
# --------------------------------------------------------------------------- #


def test_a_search_field_can_never_become_a_gmail_operator() -> None:
    """What makes searching safe is that *we* build the query, not that it is absent.

    A free-text box would forward whatever was typed — every operator Google has, and every one
    it adds later. The fields are stripped of the characters Gmail reads as syntax, so a value
    carrying a colon or a quote lands as a value, and what comes out is only ever the operators
    named here.
    """
    from app.integrations.google.gmail.manual import GmailSearchQuery, build_search_query

    wire = build_search_query(
        GmailSearchQuery(participant="devrim@oosgroup.com", subject="verhuur")
    )
    assert (
        "(from:devrim@oosgroup.com OR to:devrim@oosgroup.com OR cc:devrim@oosgroup.com)" in wire
    )
    assert 'subject:"verhuur"' in wire
    # Never the bin, never spam: this looks for a message somebody means to file.
    assert "-in:spam -in:trash" in wire

    hostile = build_search_query(
        GmailSearchQuery(participant='x" OR has:attachment larger:10M in:anywhere "')
    )
    for operator in ("has:attachment", "larger:", "in:anywhere"):
        assert operator not in hostile, hostile

    # An inclusive `before` on the screen is an exclusive one on the wire.
    dated = build_search_query(
        GmailSearchQuery(participant="a@b.nl", after=date(2026, 8, 1), before=date(2026, 8, 31))
    )
    assert "after:2026-08-01" in dated and "before:2026-09-01" in dated


async def test_search_finds_a_message_by_who_it_was_with(client_for, monkeypatch) -> None:
    """The user-facing point of #372: you no longer have to copy an id out of Gmail by hand.

    What the search returns is the same row a paste returns — the same explanation, the same
    import — so the two ways in cannot come to offer different things.
    """
    t = await make_tenant("gmail-search")
    await _seed(t)
    headers = await auth_cookie(t.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))

    async with client_for(t.host) as c:
        found = await c.get(
            "/api/v1/google/gmail/search",
            params={"participant": "nieuw@prospect.nl"},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        body = found.json()
        assert {m["message_id"] for m in body["messages"]} == {"msg-a", "msg-b"}
        # The query is echoed back: an empty result is otherwise indistinguishable from a
        # search that asked the wrong thing.
        assert "nieuw@prospect.nl" in body["query"]
        # And the explanation rides along, exactly as it does on a paste.
        first = next(m for m in body["messages"] if m["message_id"] == "msg-a")
        assert first["skip_reason"] == SkipReason.NO_EXTERNAL_MATCH.value

        # No fields at all is "list my mailbox", which is the one thing this is not.
        empty = await c.get("/api/v1/google/gmail/search", headers=headers)
        assert empty.status_code == 422
        assert empty.json()["error"]["message"] == "errors.gmail_search_empty"


async def test_explaining_a_long_thread_does_not_cost_a_query_per_message(
    client_for, monkeypatch, count_queries
) -> None:
    """The shape docs/PERFORMANCE.md calls invisible: correct at three rows, a stall at three
    hundred, and identical in every functional test either way.

    ``classify`` asks four per-row questions, which is right for the poller — it holds one
    message at a time, in a worker. Run unchanged over a fifty-message conversation inside a
    request somebody is waiting on, that is two hundred queries. ``GateCache`` moves the *data
    source* for those four and nothing about the questions, so the cost is flat in the number
    of messages instead of linear. Written down as a number, so a per-message regression has to
    argue with it.
    """
    t = await make_tenant("gmail-explain-budget")
    await _seed(t)
    headers = await auth_cookie(t.user)

    def _thread(n: int) -> _StubManualGmail:
        messages = {
            f"msg-{i}": _message(
                f"msg-{i}",
                sender="Klant <klant@client.nl>",
                subject="Lange draad",
                thread="thr-long",
                rfc822=f"<lang-{i}@client.nl>",
            )
            for i in range(n)
        }
        return _StubManualGmail(messages=messages, threads={"thr-long": list(messages)})

    async def _cost(n: int) -> int:
        monkeypatch.setattr(
            "app.integrations.google.gmail.manual.acting_as", _stub_acting_as(_thread(n))
        )
        async with client_for(t.host) as c:
            with count_queries() as counter:
                response = await c.get(
                    "/api/v1/google/gmail/threads/thr-long", headers=headers
                )
            assert response.status_code == 200, response.text
            assert len(response.json()["messages"]) == n
        return len(counter.statements)

    few, many = await _cost(3), await _cost(30)
    # Ten times the messages must not cost anything like ten times the queries.
    assert many <= few + 6, (few, many)
    assert many < 40, many


async def test_search_reads_only_the_caller_s_own_mailbox(client_for, monkeypatch) -> None:
    """Golden Rule 1 for a surface whose ids are Google's rather than ours.

    The grant comes from the *caller's* connection, so somebody in another org holds none and
    gets the module's "you have not connected Google" answer — never another mailbox's mail.
    """
    other = await make_tenant("gmail-search-other")
    headers = await auth_cookie(other.user)
    stub = _manual_stub()
    monkeypatch.setattr("app.integrations.google.gmail.manual.acting_as", _stub_acting_as(stub))
    async with client_for(other.host) as c:
        response = await c.get(
            "/api/v1/google/gmail/search",
            params={"participant": "nieuw@prospect.nl"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["error"]["message"] in (
            "errors.gmail_disabled",
            "errors.google_not_connected",
        )


# --------------------------------------------------------------------------- #
# The two skips that are worth a row (#372)
# --------------------------------------------------------------------------- #


async def test_only_the_failures_are_persisted_never_the_policy(monkeypatch) -> None:
    """A row per skip would be a log of every email the mailbox receives — the thing this
    design refuses (``gates.PERSISTED_REASONS``).

    A newsletter from nobody we know is *policy*: the dry run explains it on demand and nothing
    is stored. The poller here sees exactly that, and must leave the table empty.
    """
    t = await make_tenant("gmail-skips-policy")
    connection_id = await _seed(t)
    stub = _StubGmail(
        history=["m1"],
        messages={
            "m1": _message("m1", sender="Nieuwsbrief <news@vendor.com>", subject="Aanbieding")
        },
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(GmailSkip))).scalars().all()
    assert rows == []


async def _colleague_with_mailbox(client_for, tenant, email: str) -> None:
    """A second member whose own mailbox polls — what makes a deferral possible at all."""
    from tests.test_notification_channels import _member

    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        await _member(c, headers, email)
    await _colleague_mailbox(tenant, email, syncing=True)


async def test_a_deferral_leaves_a_row_and_the_reaper_takes_it(
    client_for, monkeypatch
) -> None:
    """The other half: two skips *are* stored, because nobody would know to look for them.

    A deferral to a colleague's mailbox is invisible by construction — the email is simply not
    there, and the person waiting for it has no reason to suspect a second mailbox is involved.
    The row carries ids, a reason and a time; the subject and participants stay in Gmail,
    fetched on demand under the user's own grant like everything else here.
    """
    t = await make_tenant("gmail-skips-defer")
    connection_id = await _seed(t)
    await _colleague_with_mailbox(client_for, t, "collega@gmail-skips-defer-example.nl")

    # Addressed to the colleague and sitting in *this* mailbox: their own poll will log it.
    stub = _StubGmail(
        history=["m9"],
        messages={
            "m9": _message(
                "m9",
                sender="Klant <klant@client.nl>",
                to="collega@gmail-skips-defer-example.nl",
                subject="Vraag",
                thread="thr-9",
            )
        },
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 0

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(GmailSkip))).scalars().one()
        assert row.reason == SkipReason.DEFERRED_TO_OWNER.value
        assert row.gmail_message_id == "m9"
        assert row.gmail_thread_id == "thr-9"
        assert row.detail == {"owner": "collega@gmail-skips-defer-example.nl"}
        # Ids, a reason and a time — and nothing that came out of the message.
        assert "Vraag" not in str(row.detail)

        # Retention: a permanent record of a transient failure is a log by another name.
        row.created_at = datetime.now(UTC) - timedelta(days=SKIP_RETENTION_DAYS + 1)
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await reap_skips(t.org, session)
        await session.commit()
        assert (await session.execute(select(GmailSkip))).scalars().all() == []


async def test_a_re_offered_message_leaves_one_row_not_one_per_poll(
    client_for, monkeypatch
) -> None:
    """The upsert, and why the retention window means what it says.

    A deferred message is re-offered on every poll until the other mailbox takes it. Appending
    a row each time would turn a bounded record into the volume problem this whole design is
    avoiding, and would make "kept for 60 days" false — the newest row keeping the oldest alive.
    """
    t = await make_tenant("gmail-skips-upsert")
    connection_id = await _seed(t)
    await _colleague_with_mailbox(client_for, t, "collega@gmail-skips-upsert-example.nl")

    def _stub() -> _StubGmail:
        return _StubGmail(
            history=["m7"],
            messages={
                "m7": _message(
                    "m7",
                    sender="Klant <klant@client.nl>",
                    to="collega@gmail-skips-upsert-example.nl",
                    thread="t7",
                )
            },
        )

    await _poll(t, connection_id, _stub(), monkeypatch)
    await _poll(t, connection_id, _stub(), monkeypatch)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(GmailSkip))).scalars().all()
    assert len(rows) == 1


async def test_skip_rows_are_tenant_isolated(client_for, monkeypatch) -> None:
    """Golden Rule 1, on the new table."""
    t = await make_tenant("gmail-skips-rls-a")
    connection_id = await _seed(t)
    await _colleague_with_mailbox(client_for, t, "collega@gmail-skips-rls-a-example.nl")
    stub = _StubGmail(
        history=["m5"],
        messages={
            "m5": _message(
                "m5",
                sender="Klant <klant@client.nl>",
                to="collega@gmail-skips-rls-a-example.nl",
                thread="t5",
            )
        },
    )
    await _poll(t, connection_id, stub, monkeypatch)

    other = await make_tenant("gmail-skips-rls-b")
    async with async_session_maker() as session:
        await set_current_org(session, other.org.id)
        assert (await session.execute(select(GmailSkip))).scalars().all() == []
