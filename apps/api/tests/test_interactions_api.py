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
                "kind": "meeting",
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
            await c.post(
                f"/api/v1/interactions/{row_id}/remap", json={}, headers=owner_headers
            )
        ).status_code == 403

        # The mailbox owner approves: status flips and the body-fetch event fires.
        approved = await c.post(
            f"/api/v1/interactions/{row_id}/approve", headers=member_headers
        )
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
        mention_rows = [
            n for n in inbox["items"] if n["event_type"] == "interactions.mentioned"
        ]
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
        assert (
            len([n for n in inbox["items"] if n["event_type"] == "interactions.mentioned"])
            == 1
        )


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
                    "kind": "meeting",
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
