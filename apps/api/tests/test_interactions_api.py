"""Interactions module (#22): manual CRUD, gmail review flow (owner-only), isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.auth.models import User
from app.core.events import subscribe
from app.db import async_session_maker, set_current_org
from app.modules.interactions import system as interactions_system
from tests.conftest import auth_cookie, make_tenant

_NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


async def _seed_gmail_row(
    tenant,
    owner_user_id: uuid.UUID,
    *,
    pending: bool = True,
    message_id: str = "msg-1",
    thread_id: str = "thr-1",
    mappings: dict | None = None,
) -> str:
    """Insert a gmail-sourced interaction the way the (later) gmail poller does."""
    from app.core.events import SystemContext

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        ctx = SystemContext(org=tenant.org, session=session)
        row = await interactions_system.record_email(
            ctx,
            owner_user_id=owner_user_id,
            owner_name="Mailbox Owner",
            occurred_at=_NOW,
            subject="Offerte akkoord",
            snippet="Bij deze akkoord op de offerte...",
            direction="inbound",
            participants=[{"email": "klant@client.nl", "name": "Klant", "role": "from"}],
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            rfc822_message_id=f"<{message_id}@mail.example>",
            deep_link="https://mail.google.com/mail/u/0/#all/abc",
            pending=pending,
            mappings=mappings or {},
        )
        await session.commit()
        return str(row.id)


async def test_manual_crud_derives_company_and_filters(client_for) -> None:
    t = await make_tenant("inter-crud")
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
            "/api/v1/interactions",
            json={
                "kind": "physical_meeting",
                "occurred_at": _NOW.isoformat(),
                "subject": "Kick-off",
                "body_text": "Besproken: planning en scope.",
                "project_id": project["id"],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        row = created.json()
        # A project link fills the company link, so the client timeline stays complete.
        assert row["company_id"] == company["id"]
        assert row["status"] == "logged" and row["source"] == "manual"
        assert row["owner_name"]

        # Feed filters: by company, by kind.
        by_company = (
            await c.get(
                "/api/v1/interactions",
                params={"company_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert by_company["total"] == 1
        assert (
            await c.get("/api/v1/interactions", params={"kind": "call"}, headers=headers)
        ).json()["total"] == 0

        updated = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"kind": "call", "direction": "outbound"},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["kind"] == "call"

        # The edit landed in the activity trail (§16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "interaction", "entity_id": row["id"]},
                headers=headers,
            )
        ).json()
        assert [e["action"] for e in trail] == ["updated", "created"]

        assert (
            await c.delete(f"/api/v1/interactions/{row['id']}", headers=headers)
        ).status_code == 204


async def test_interaction_kinds_tenant_configurable(client_for) -> None:
    """#174: kinds seed lazily per org (meeting split into online/physical), custom kinds
    become loggable, the retired plain "meeting" no longer validates, email is protected,
    an in-use kind refuses deletion, and the catalog is tenant-isolated."""
    t = await make_tenant("inter-kinds-a")
    other = await make_tenant("inter-kinds-b")
    headers = await auth_cookie(t.user)
    other_headers = await auth_cookie(other.user)
    async with client_for(t.host) as c:
        kinds = (await c.get("/api/v1/interactions/kinds", headers=headers)).json()
        assert {k["key"] for k in kinds} == {
            "email",
            "online_meeting",
            "physical_meeting",
            "call",
            "note",
        }

        # The retired hardcoded kind is gone; the split halves and custom kinds work.
        base = {"occurred_at": _NOW.isoformat(), "subject": "Soorten"}
        assert (
            await c.post("/api/v1/interactions", json={"kind": "meeting", **base}, headers=headers)
        ).status_code == 422
        assert (
            await c.post(
                "/api/v1/interactions", json={"kind": "online_meeting", **base}, headers=headers
            )
        ).status_code == 201
        created_kind = await c.post(
            "/api/v1/interactions/kinds",
            json={
                "key": "site_visit",
                "label_i18n": {"nl": "Locatiebezoek", "en": "Site visit"},
            },
            headers=headers,
        )
        assert created_kind.status_code == 201, created_kind.text
        row = await c.post(
            "/api/v1/interactions", json={"kind": "site_visit", **base}, headers=headers
        )
        assert row.status_code == 201

        # In use → deletion refused; deactivation hides it from new writes.
        kind_id = created_kind.json()["id"]
        assert (
            await c.delete(f"/api/v1/interactions/kinds/{kind_id}", headers=headers)
        ).status_code == 409
        assert (
            await c.patch(
                f"/api/v1/interactions/kinds/{kind_id}", json={"active": False}, headers=headers
            )
        ).status_code == 200
        assert (
            await c.post(
                "/api/v1/interactions", json={"kind": "site_visit", **base}, headers=headers
            )
        ).status_code == 422
        # Editing an existing row while keeping its now-deactivated kind still works.
        assert (
            await c.patch(
                f"/api/v1/interactions/{row.json()['id']}",
                json={"kind": "site_visit", "subject": "Nog steeds"},
                headers=headers,
            )
        ).status_code == 200

        # email is system-owned: relabel fine, deactivate/delete refused.
        email_kind = next(k for k in kinds if k["key"] == "email")
        assert (
            await c.patch(
                f"/api/v1/interactions/kinds/{email_kind['id']}",
                json={"label_i18n": {"nl": "Mail", "en": "Mail"}},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await c.patch(
                f"/api/v1/interactions/kinds/{email_kind['id']}",
                json={"active": False},
                headers=headers,
            )
        ).status_code == 409
        assert (
            await c.delete(f"/api/v1/interactions/kinds/{email_kind['id']}", headers=headers)
        ).status_code == 409

    # Tenant isolation: the other org seeds its own defaults, never sees site_visit.
    async with client_for(other.host) as cb:
        other_kinds = (await cb.get("/api/v1/interactions/kinds", headers=other_headers)).json()
        assert "site_visit" not in {k["key"] for k in other_kinds}
        assert (
            await cb.patch(
                f"/api/v1/interactions/kinds/{kind_id}",
                json={"active": True},
                headers=other_headers,
            )
        ).status_code == 404


async def test_log_time_creates_linked_entry_that_survives_deletion(client_for) -> None:
    """#175: `log_time` on a manual interaction creates a time entry in the same request,
    carrying the interaction's links and subject; deleting the interaction later detaches
    the link (SET NULL) and never deletes the logged hours."""
    t = await make_tenant("inter-logtime")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        created = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Belafspraak",
                "company_id": company["id"],
                "log_time": {
                    "started_at": "2026-07-10T14:00:00Z",
                    "ended_at": "2026-07-10T14:45:00Z",
                },
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        row = created.json()

        entries = (await c.get("/api/v1/time/entries", headers=headers)).json()
        assert entries["total"] == 1
        entry = entries["items"][0]
        assert entry["interaction_id"] == row["id"]
        assert entry["company_id"] == company["id"]
        assert entry["description"] == "Belafspraak"
        assert entry["minutes"] == 45
        # The interaction's kind is mirrored into a time-entry type on first use (#182): the
        # entry is typed "call", and a matching Uren-type now exists carrying the kind's label.
        assert entry["entry_type_key"] == "call"
        types = (await c.get("/api/v1/time/entry-types", headers=headers)).json()
        call_type = next((et for et in types if et["key"] == "call"), None)
        assert call_type is not None and call_type["label_i18n"]["nl"] == "Telefoongesprek"
        # The default work/email types were seeded alongside, not skipped.
        assert {"work", "email"} <= {et["key"] for et in types}

        # Deleting the interaction detaches, never deletes, the logged hours.
        assert (
            await c.delete(f"/api/v1/interactions/{row['id']}", headers=headers)
        ).status_code == 204
        entries = (await c.get("/api/v1/time/entries", headers=headers)).json()
        assert entries["total"] == 1
        assert entries["items"][0]["interaction_id"] is None


async def test_log_time_respects_a_deactivated_matching_type(client_for) -> None:
    """#182: mirroring a kind into a time-entry type must not resurrect one an admin
    deliberately deactivated — the logged entry stays untyped instead."""
    t = await make_tenant("inter-logtime-deact")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Create then deactivate a "call" time-entry type.
        created_type = (
            await c.post(
                "/api/v1/time/entry-types",
                json={"key": "call", "label_i18n": {"nl": "Bellen", "en": "Call"}},
                headers=headers,
            )
        ).json()
        assert (
            await c.patch(
                f"/api/v1/time/entry-types/{created_type['id']}",
                json={"active": False},
                headers=headers,
            )
        ).status_code == 200

        created = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Belafspraak",
                "log_time": {
                    "started_at": "2026-07-10T14:00:00Z",
                    "ended_at": "2026-07-10T14:30:00Z",
                },
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        entry = (await c.get("/api/v1/time/entries", headers=headers)).json()["items"][0]
        assert entry["entry_type_key"] is None  # deactivation respected, not resurrected


async def test_manual_email_kind_refused(client_for) -> None:
    t = await make_tenant("inter-email-kind")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        resp = await c.post(
            "/api/v1/interactions",
            json={"kind": "email", "occurred_at": _NOW.isoformat(), "subject": "Handmatig"},
            headers=headers,
        )
        assert resp.status_code == 422


async def test_member_edits_own_admin_edits_any(client_for) -> None:
    t = await make_tenant("inter-scope")  # owner
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "lid@inter-scope.example")
        member_headers = await auth_cookie(member)
        mine = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "note", "occurred_at": _NOW.isoformat(), "subject": "Eigen notitie"},
                headers=member_headers,
            )
        ).json()
        owners = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "note", "occurred_at": _NOW.isoformat(), "subject": "Van de baas"},
                headers=owner_headers,
            )
        ).json()

        # A member holds write:own — someone else's row reads as absent (404, not 403).
        assert (
            await c.patch(
                f"/api/v1/interactions/{owners['id']}",
                json={"subject": "Kaping"},
                headers=member_headers,
            )
        ).status_code == 404
        assert (
            await c.patch(
                f"/api/v1/interactions/{mine['id']}",
                json={"subject": "Eigen notitie 2"},
                headers=member_headers,
            )
        ).status_code == 200
        # The owner role holds :any and may edit the member's row.
        assert (
            await c.patch(
                f"/api/v1/interactions/{mine['id']}",
                json={"subject": "Bijgewerkt door admin"},
                headers=owner_headers,
            )
        ).status_code == 200


async def test_pending_rows_private_to_owner_until_approved(client_for) -> None:
    """#172: a pending gmail row is invisible — not just body-redacted — to anyone but its
    mailbox owner; #168: the owner filter needs read_all, and a wildcard/admin viewer may
    still audit another mailbox's pending queue. Approval restores team visibility."""
    t = await make_tenant("inter-pending-priv")
    owner_headers = await auth_cookie(t.user)  # org owner: "*" satisfies read_all
    async with client_for(t.host) as c:
        mailbox = await _member(c, owner_headers, "mailbox@priv.example")
        colleague = await _member(c, owner_headers, "collega@priv.example")
        mailbox_headers = await auth_cookie(mailbox)
        colleague_headers = await auth_cookie(colleague)
        row_id = await _seed_gmail_row(t, mailbox.id, pending=True)

        # The owner of the mailbox sees their pending row; a plain colleague sees nothing.
        assert (
            await c.get(
                "/api/v1/interactions",
                params={"mine": True, "status": "pending"},
                headers=mailbox_headers,
            )
        ).json()["total"] == 1
        assert (
            await c.get(
                "/api/v1/interactions", params={"status": "pending"}, headers=colleague_headers
            )
        ).json()["total"] == 0
        assert (await c.get("/api/v1/interactions", headers=colleague_headers)).json()["total"] == 0
        assert (
            await c.get(f"/api/v1/interactions/{row_id}", headers=colleague_headers)
        ).status_code == 404

        # Someone else's queue is not a filter a plain member may use (#168)...
        assert (
            await c.get(
                "/api/v1/interactions",
                params={"owner_user_id": str(mailbox.id)},
                headers=colleague_headers,
            )
        ).status_code == 403
        # ...but a read_all holder may audit it, pending rows included.
        assert (
            await c.get(
                "/api/v1/interactions",
                params={"owner_user_id": str(mailbox.id), "status": "pending"},
                headers=owner_headers,
            )
        ).json()["total"] == 1

        # Approval makes it team-visible, exactly as before.
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=mailbox_headers)
        ).status_code == 200
        assert (await c.get("/api/v1/interactions", headers=colleague_headers)).json()["total"] == 1


async def test_list_free_text_search(client_for) -> None:
    """#168: `q` matches subject, snippet and body, case-insensitively."""
    t = await make_tenant("inter-search")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for subject, body in (("Offerte besproken", "Alles akkoord"), ("Storing", "DNS lag om")):
            assert (
                await c.post(
                    "/api/v1/interactions",
                    json={
                        "kind": "call",
                        "occurred_at": _NOW.isoformat(),
                        "subject": subject,
                        "body_text": body,
                    },
                    headers=headers,
                )
            ).status_code == 201
        assert (
            await c.get("/api/v1/interactions", params={"q": "offerte"}, headers=headers)
        ).json()["total"] == 1
        assert (await c.get("/api/v1/interactions", params={"q": "dns"}, headers=headers)).json()[
            "total"
        ] == 1
        assert (await c.get("/api/v1/interactions", params={"q": "niets"}, headers=headers)).json()[
            "total"
        ] == 0


async def test_gmail_review_is_strictly_owner_only(client_for) -> None:
    t = await make_tenant("inter-review")  # t.user = owner, holds "*"
    owner_headers = await auth_cookie(t.user)

    approved_events: list[dict] = []
    subscribe("interaction.approved", _collect(approved_events))

    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@inter-review.example")
        member_headers = await auth_cookie(member)
        row_id = await _seed_gmail_row(t, member.id, pending=True)
        # Pending: the team sees metadata, never a body; edit/delete are closed.
        listed = (
            await c.get("/api/v1/interactions", params={"status": "pending"}, headers=owner_headers)
        ).json()
        assert listed["total"] == 1
        assert listed["items"][0]["snippet"] and listed["items"][0]["body_text"] is None
        assert (
            await c.patch(
                f"/api/v1/interactions/{row_id}", json={"subject": "X"}, headers=member_headers
            )
        ).status_code == 409
        assert (
            await c.delete(f"/api/v1/interactions/{row_id}", headers=member_headers)
        ).status_code == 409

        # Even a wildcard-holding owner cannot decide about someone else's mailbox.
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=owner_headers)
        ).status_code == 403
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/remap", json={}, headers=owner_headers)
        ).status_code == 403

        # The mailbox owner approves: status flips and the body-fetch event fires.
        approved = await c.post(f"/api/v1/interactions/{row_id}/approve", headers=member_headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "logged"
        assert len(approved_events) == 1
        assert str(approved_events[0]["interaction_id"]) == row_id
        # Approving twice is a conflict, not a second event.
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=member_headers)
        ).status_code == 409


async def test_gmail_reject_removes_metadata_and_emits_suppression(client_for) -> None:
    t = await make_tenant("inter-reject")
    owner_headers = await auth_cookie(t.user)

    rejected_events: list[dict] = []
    subscribe("interaction.rejected", _collect(rejected_events))

    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@inter-reject.example")
        member_headers = await auth_cookie(member)
        row_id = await _seed_gmail_row(t, member.id, pending=True, message_id="msg-rej")
        resp = await c.post(
            f"/api/v1/interactions/{row_id}/reject",
            json={"suppress_thread": True},
            headers=member_headers,
        )
        assert resp.status_code == 204, resp.text
        # Metadata is gone too — rejection leaves nothing on the timeline.
        assert (
            await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)
        ).status_code == 404
        assert len(rejected_events) == 1
        assert rejected_events[0]["gmail_message_id"] == "msg-rej"
        assert rejected_events[0]["suppress_thread"] is True


async def test_remap_by_owner_moves_links(client_for) -> None:
    t = await make_tenant("inter-remap")
    headers = await auth_cookie(t.user)
    row_id = await _seed_gmail_row(t, t.user.id, pending=True, message_id="msg-remap")
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Doelklant"}, headers=headers)
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Opvolgen", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        remapped = await c.post(
            f"/api/v1/interactions/{row_id}/remap",
            json={"task_id": task["id"]},
            headers=headers,
        )
        assert remapped.status_code == 200, remapped.text
        body = remapped.json()
        assert body["task_id"] == task["id"]
        # The task's company rides along so the client timeline picks the email up.
        assert body["company_id"] == company["id"]


async def test_approve_can_assign_links_in_one_step(client_for) -> None:
    """#183: approving a pending email can optionally set its links in the same request —
    no approve-then-reopen-and-move. The picked task derives the client, and the row lands
    logged and team-visible."""
    t = await make_tenant("inter-approve-assign")
    headers = await auth_cookie(t.user)
    row_id = await _seed_gmail_row(t, t.user.id, pending=True, message_id="msg-appr")
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Doelklant"}, headers=headers)
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Opvolgen", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        approved = await c.post(
            f"/api/v1/interactions/{row_id}/approve",
            json={"task_id": task["id"]},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "logged"
        assert body["task_id"] == task["id"]
        assert body["company_id"] == company["id"]  # derived from the task
        # The assignment landed on the interaction's own trail alongside the approval.
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "interaction", "entity_id": row_id},
                headers=headers,
            )
        ).json()
        assert "updated" in [e["action"] for e in trail]


async def test_plain_approve_still_works_without_a_body(client_for) -> None:
    """#183: the one-click approve (no links) is unchanged."""
    t = await make_tenant("inter-approve-plain")
    headers = await auth_cookie(t.user)
    row_id = await _seed_gmail_row(t, t.user.id, pending=True, message_id="msg-plain")
    async with client_for(t.host) as c:
        approved = await c.post(f"/api/v1/interactions/{row_id}/approve", headers=headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "logged"


async def test_interactions_tenant_isolation(client_for) -> None:
    a = await make_tenant("inter-iso-a")
    b = await make_tenant("inter-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        row = (
            await ca.post(
                "/api/v1/interactions",
                json={"kind": "call", "occurred_at": _NOW.isoformat(), "subject": "Belnotitie"},
                headers=a_headers,
            )
        ).json()
    async with client_for(b.host) as cb:
        assert (
            await cb.get(f"/api/v1/interactions/{row['id']}", headers=b_headers)
        ).status_code == 404
        assert (await cb.get("/api/v1/interactions", headers=b_headers)).json()["total"] == 0


def _collect(into: list[dict]):
    async def _handler(ctx, payload):  # noqa: ANN001
        into.append(payload)

    return _handler


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Mailbox Owner", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def test_mentions_in_note_validate_store_and_notify(client_for) -> None:
    """#151: `@[Name](mention:<uuid>)` markers in a manual note are membership-validated,
    stored structurally, and notify the mentioned member — a foreign uuid does nothing, an
    edit only pings people mentioned for the first time."""
    from tests.test_notifications_fanout import _member

    t = await make_tenant("inter-mentions")
    colleague = await _member(t, "collega@example.com")
    stranger_id = uuid.uuid4()  # nobody in this org — must be dropped, never notified
    headers = await auth_cookie(t.user)
    colleague_headers = await auth_cookie(colleague)

    def marker(uid) -> str:
        return f"@[Collega](mention:{uid})"

    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "note",
                "occurred_at": _NOW.isoformat(),
                "subject": "Notitie",
                "body_text": f"Afgestemd met {marker(colleague.id)} en {marker(stranger_id)}.",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        row = created.json()

        inbox = (await c.get("/api/v1/notifications", headers=colleague_headers)).json()
        mention_rows = [n for n in inbox["items"] if n["event_type"] == "interactions.mentioned"]
        assert len(mention_rows) == 1
        assert mention_rows[0]["payload"]["subject"] == "Notitie"

        # Re-saving the same body must not re-notify the same person.
        updated = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"body_text": f"Afgestemd met {marker(colleague.id)}. Bijgewerkt."},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        inbox = (await c.get("/api/v1/notifications", headers=colleague_headers)).json()
        assert len([n for n in inbox["items"] if n["event_type"] == "interactions.mentioned"]) == 1


async def test_logging_and_moving_mirror_onto_host_trails(client_for) -> None:
    """#152: a logged contactmoment shows on the host records' activity trails, and a move
    tells both sides — the interaction's own field-diff trail stays where it was."""
    t = await make_tenant("inter-host-trail")
    headers = await auth_cookie(t.user)

    async def trail(c, entity_type: str, entity_id: str) -> list[str]:
        rows = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": entity_type, "entity_id": entity_id},
                headers=headers,
            )
        ).json()
        return [e["action"] for e in rows]

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        other = (
            await c.post("/api/v1/companies", json={"name": "Andere BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=headers,
            )
        ).json()

        row = (
            await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "call",
                    "occurred_at": _NOW.isoformat(),
                    "subject": "Belafspraak",
                    "project_id": project["id"],
                },
                headers=headers,
            )
        ).json()

        # Logged on both hosts (company was derived from the project).
        assert "interaction.logged" in await trail(c, "project", project["id"])
        assert "interaction.logged" in await trail(c, "company", company["id"])

        # Moving the client link tells both sides.
        moved = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"company_id": other["id"], "project_id": None},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert "interaction.unlinked" in await trail(c, "company", company["id"])
        assert "interaction.linked" in await trail(c, "company", other["id"])
        assert "interaction.unlinked" in await trail(c, "project", project["id"])


async def test_project_rollup_includes_task_interactions_with_labels(client_for) -> None:
    """#147: `?project_id=X&include=tasks` returns the project's own rows plus its tasks',
    each carrying the linked record's label so the web can draw chips without lookups."""
    t = await make_tenant("inter-rollup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Review",
                    "project_id": project["id"],
                    "company_id": company["id"],
                },
                headers=headers,
            )
        ).json()

        await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Taakoverleg",
                "task_id": task["id"],
            },
            headers=headers,
        )

        # Without the roll-up the task-linked row is invisible on the project…
        flat = (
            await c.get(
                "/api/v1/interactions",
                params={"project_id": project["id"]},
                headers=headers,
            )
        ).json()
        assert flat["total"] == 0

        # …with it, it shows and names its task and (derived) client.
        rolled = (
            await c.get(
                "/api/v1/interactions",
                params={"project_id": project["id"], "include": "tasks"},
                headers=headers,
            )
        ).json()
        assert rolled["total"] == 1
        row = rolled["items"][0]
        assert row["task_title"] == "Review"
        assert row["company_name"] == "Klant BV"

        # Tenant isolation: the same query from another org sees nothing.
        other = await make_tenant("inter-rollup-b")
        other_headers = await auth_cookie(other.user)
        async with client_for(other.host) as c2:
            foreign = (
                await c2.get(
                    "/api/v1/interactions",
                    params={"project_id": project["id"], "include": "tasks"},
                    headers=other_headers,
                )
            ).json()
            assert foreign["total"] == 0


async def test_participants_resolve_to_org_contacts_at_read_time(client_for) -> None:
    """#160: participant addresses are matched against org contacts when the row is read —
    a contact created *after* the email was logged still links up; a stranger stays bare."""
    t = await make_tenant("inter-participants")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        row = (
            await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "physical_meeting",
                    "occurred_at": _NOW.isoformat(),
                    "subject": "Kennismaking",
                    "participants": [
                        {"email": "Anna@Klant.NL", "name": "Anna", "role": "to"},
                        {"email": "cc@elders.example", "role": "cc"},
                    ],
                },
                headers=headers,
            )
        ).json()

        # Not a contact yet: both addresses read unresolved.
        fetched = (await c.get(f"/api/v1/interactions/{row['id']}", headers=headers)).json()
        assert all(p["contact_id"] is None for p in fetched["participants"])

        # The contact arrives later (different case): the address now resolves.
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Anna", "email": "anna@klant.nl"},
                headers=headers,
            )
        ).json()
        fetched = (await c.get(f"/api/v1/interactions/{row['id']}", headers=headers)).json()
        # EmailStr normalises the stored address's domain case — match case-insensitively.
        by_email = {p["email"].lower(): p for p in fetched["participants"]}
        assert by_email["anna@klant.nl"]["contact_id"] == contact["id"]
        assert by_email["cc@elders.example"]["contact_id"] is None


async def test_contact_mentions_validate_and_store(client_for) -> None:
    """#165: `@[Name](mention:contact:<uuid>)` markers store structurally in
    mentioned_contact_ids, org-validated — a contact id from another org never survives —
    and untyped markers keep meaning colleagues (no reinterpretation of stored bodies)."""
    other = await make_tenant("inter-cmention-b")
    t = await make_tenant("inter-cmention-a")
    headers = await auth_cookie(t.user)
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as cb:
        foreign_contact = (
            await cb.post("/api/v1/contacts", json={"first_name": "Vreemd"}, headers=other_headers)
        ).json()
    async with client_for(t.host) as c:
        contact = (
            await c.post("/api/v1/contacts", json={"first_name": "Anna"}, headers=headers)
        ).json()
        body = (
            f"Gesproken met @[Anna](mention:contact:{contact['id']}) "
            f"en @[Vreemd](mention:contact:{foreign_contact['id']}) "
            f"en @[Ik](mention:{t.user.id})"
        )
        created = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Vermeldingen",
                "body_text": body,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            from app.modules.interactions.models import Interaction

            row = await session.get(Interaction, uuid.UUID(created.json()["id"]))
            assert row.mentioned_contact_ids == [contact["id"]]
            assert row.mentioned_user_ids == [str(t.user.id)]


async def test_participants_resolve_to_org_members_at_read_time(client_for) -> None:
    """#167: a participant address belonging to an org employee resolves as ``user_id`` —
    a colleague, never a "create a contact" prompt — and only within the own org: another
    tenant's member email stays unresolved here."""
    other = await make_tenant("inter-members-b")  # their owner's email must not resolve in A
    t = await make_tenant("inter-members-a")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        colleague = await _member(c, headers, "collega@bureau.example")
        row = (
            await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "physical_meeting",
                    "occurred_at": _NOW.isoformat(),
                    "subject": "Interne afstemming met klant erbij",
                    "participants": [
                        {"email": "Collega@Bureau.example", "name": "Collega", "role": "to"},
                        {"email": other.user.email, "role": "cc"},
                        {"email": "klant@elders.example", "role": "from"},
                    ],
                },
                headers=headers,
            )
        ).json()
        fetched = (await c.get(f"/api/v1/interactions/{row['id']}", headers=headers)).json()
        by_email = {p["email"].lower(): p for p in fetched["participants"]}
        assert by_email["collega@bureau.example"]["user_id"] == str(colleague.id)
        assert by_email[other.user.email.lower()]["user_id"] is None
        assert by_email["klant@elders.example"]["user_id"] is None


async def test_thread_followup_inherits_all_links_including_task(client_for) -> None:
    """#157 addendum: a reply in a Gmail thread lands where the original lives — thread
    inheritance copies *all four* links (task and project included), from the newest
    logged row, so a moved original re-aims future replies."""
    from app.core.events import SystemContext

    t = await make_tenant("inter-thread-inherit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Review", "project_id": project["id"]},
                headers=headers,
            )
        ).json()

    await _seed_gmail_row(
        t,
        t.user.id,
        pending=False,
        message_id="orig-1",
        thread_id="thr-inherit",
        mappings={
            "company_id": uuid.UUID(company["id"]),
            "project_id": uuid.UUID(project["id"]),
            "task_id": uuid.UUID(task["id"]),
        },
    )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        inherited = await interactions_system.thread_mappings(ctx, "thr-inherit")
        assert inherited is not None
        assert inherited["task_id"] == uuid.UUID(task["id"])
        assert inherited["project_id"] == uuid.UUID(project["id"])
        assert inherited["company_id"] == uuid.UUID(company["id"])


async def test_kind_defaults_reconciled_for_existing_org(client_for) -> None:
    """#184: an org seeded before the online/physical meeting split (or otherwise missing a
    default kind) gains it on the next kinds fetch — the reconciler inserts missing keys rather
    than skipping the moment the org already holds *any* kinds. Previously such an org could never
    offer *Online afspraak* / *Afspraak op locatie*."""
    from sqlalchemy import delete as sql_delete

    from app.modules.interactions.models import InteractionKindDef

    t = await make_tenant("inter-kind-reconcile")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # First fetch seeds all five system kinds.
        assert (await c.get("/api/v1/interactions/kinds", headers=headers)).status_code == 200
        # Simulate an org stuck without the meeting split.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                sql_delete(InteractionKindDef).where(
                    InteractionKindDef.org_id == t.org.id,
                    InteractionKindDef.key.in_(["online_meeting", "physical_meeting"]),
                )
            )
            await session.commit()
        # The next fetch reconciles the missing kinds back in — not a one-shot "seed when empty".
        reconciled = (await c.get("/api/v1/interactions/kinds", headers=headers)).json()
        keys = {k["key"] for k in reconciled}
        assert "online_meeting" in keys and "physical_meeting" in keys


async def test_interaction_reports_closes_task(client_for) -> None:
    """#157: an interaction designated as a task's closing contact moment reports
    ``closes_task=true``; a merely-linked one reports false. The lookup is org-scoped, so a task
    in another org never flips the flag."""
    from app.modules.tasks.models import Task

    t = await make_tenant("inter-closes")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Review", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        base = {"occurred_at": _NOW.isoformat(), "task_id": task["id"]}
        closing = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "call", "subject": "Besproken", **base},
                headers=headers,
            )
        ).json()
        other = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "note", "subject": "Los", **base},
                headers=headers,
            )
        ).json()

        # Designate the closing moment on the task (what CloseTaskDialog does).
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            row = await session.get(Task, uuid.UUID(task["id"]))
            row.closing_interaction_id = uuid.UUID(closing["id"])
            await session.commit()

        assert (await c.get(f"/api/v1/interactions/{closing['id']}", headers=headers)).json()[
            "closes_task"
        ] is True
        assert (await c.get(f"/api/v1/interactions/{other['id']}", headers=headers)).json()[
            "closes_task"
        ] is False


async def test_task_host_activity_readable(client_for) -> None:
    """#152: a contact moment logged against a task is mirrored onto the core activity log under
    entity_type=task, and — because tasks now register as auditable — that mirror entry is
    readable via the activity endpoint (which refuses unregistered entity types). This is what
    lets the task page show 'contactmoment gelogd' like the company/project/contact panels."""
    t = await make_tenant("inter-task-activity")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post("/api/v1/tasks", json={"title": "Bouwen"}, headers=headers)
        ).json()
        moment = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Kickoff",
                "task_id": task["id"],
            },
            headers=headers,
        )
        assert moment.status_code == 201, moment.text

        feed = (
            await c.get(
                f"/api/v1/activity?entity_type=task&entity_id={task['id']}", headers=headers
            )
        ).json()
        logged = [a for a in feed if a["action"] == "interaction.logged"]
        assert logged, "task activity feed should surface the mirrored contact moment"
        assert logged[0]["payload"]["subject"] == "Kickoff"
        assert logged[0]["payload"]["interaction_id"] == moment.json()["id"]
