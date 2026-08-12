"""Bulk review (#299): approving, filing and rejecting a whole selection at once.

The queue these serve is forty auto-matched emails, so the tests are mostly about the ways a
batch could quietly mean something other than N clicks: overwriting links nobody chose,
reviewing a colleague's mailbox, rolling forty-nine good rows back for one stale one, or
crossing a tenant because the ids came from the caller rather than from a filter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.events import subscribe
from tests.conftest import auth_cookie, make_tenant
from tests.test_interactions_api import _collect, _member, _seed_gmail_row

_NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


async def test_bulk_approve_keeps_each_rows_own_links_when_none_are_sent(client_for) -> None:
    """The central promise: approving in bulk is a *status* change.

    Every row keeps whatever the gmail matcher derived for it, so a batch never blanket-files
    forty emails onto one client. This is what makes the connect step survive bulk approval.
    """
    t = await make_tenant("bulk-keep")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-keep.example")
        member_headers = await auth_cookie(member)
        acme = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=owner_headers)
        ).json()
        globex = (
            await c.post("/api/v1/companies", json={"name": "Globex"}, headers=owner_headers)
        ).json()

        # Three pending rows the poller already matched to *different* clients, plus one it
        # could not place at all — the realistic shape of a review queue.
        matched_acme = await _seed_gmail_row(
            t, member.id, message_id="m1", thread_id="t1", mappings={"company_id": acme["id"]}
        )
        matched_globex = await _seed_gmail_row(
            t, member.id, message_id="m2", thread_id="t2", mappings={"company_id": globex["id"]}
        )
        unmatched = await _seed_gmail_row(t, member.id, message_id="m3", thread_id="t3")

        result = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": [matched_acme, matched_globex, unmatched]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 3, "failed": []}

        rows = {
            row_id: (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()
            for row_id in (matched_acme, matched_globex, unmatched)
        }
        assert all(row["status"] == "logged" for row in rows.values())
        # Each kept its own match; the unmatched one stayed unmatched rather than inheriting
        # a neighbour's client.
        assert rows[matched_acme]["company_id"] == acme["id"]
        assert rows[matched_globex]["company_id"] == globex["id"]
        assert rows[unmatched]["company_id"] is None


async def test_bulk_approve_can_file_the_whole_batch_in_one_step(client_for) -> None:
    """The other order: a run of emails that *is* all one client's, filed while approving."""
    t = await make_tenant("bulk-file")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-file.example")
        member_headers = await auth_cookie(member)
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=owner_headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Website", "company_id": company["id"]},
                headers=owner_headers,
            )
        ).json()
        ids = [
            await _seed_gmail_row(t, member.id, message_id=f"m{i}", thread_id=f"t{i}")
            for i in range(3)
        ]

        result = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": ids, "project_id": project["id"]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 3
        for row_id in ids:
            row = (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()
            assert row["status"] == "logged"
            assert row["project_id"] == project["id"]
            # The client is still derived from the project, exactly as one approve derives it.
            assert row["company_id"] == company["id"]


async def test_a_batch_can_name_a_roster_and_an_unsent_one_leaves_each_row_alone(
    client_for,
) -> None:
    """#300 through the batch: filing a run of emails onto the people who were in them.

    The roster obeys the same absent-means-leave-alone rule as the links, and for the same
    reason — a bulk dialog starts blank over rows that disagree, so treating "sent nothing" as
    "empty the roster" would strip the matcher's own contact off every row the user did not
    look at. It is resolved once for the call, so an unseeable id is a 422 for the payload
    rather than fifty identical row failures.
    """
    t = await make_tenant("bulk-roster")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-roster.example")
        member_headers = await auth_cookie(member)
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=owner_headers)
        ).json()
        people = [
            (
                await c.post(
                    "/api/v1/contacts",
                    json={"first_name": name, "company_ids": [company["id"]]},
                    headers=owner_headers,
                )
            ).json()
            for name in ("Jan", "Piet")
        ]
        ids = [
            await _seed_gmail_row(t, member.id, message_id=f"m{i}", thread_id=f"t{i}")
            for i in range(2)
        ]

        result = await c.post(
            "/api/v1/interactions/bulk/assign",
            json={"ids": ids, "contact_ids": [p["id"] for p in people]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 2
        for row_id in ids:
            row = (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()
            assert [p["id"] for p in row["contacts"]] == [p["id"] for p in people]
            # Chip 0 is the lead the column mirrors, in the batch exactly as in one write.
            assert row["contact_id"] == people[0]["id"]
            # Filing did not approve them — that is the other endpoint.
            assert row["status"] == "pending"

        # Approving afterwards sends no contact fields at all, and must keep what was filed.
        approved = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": ids},
            headers=member_headers,
        )
        assert approved.status_code == 200, approved.text
        for row_id in ids:
            row = (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()
            assert row["status"] == "logged"
            assert [p["id"] for p in row["contacts"]] == [p["id"] for p in people]

        # A contact this tenant cannot see fails the call, not the rows (Golden Rule 1).
        other = await make_tenant("bulk-roster-other", email="other@bulk-roster.example")
        async with client_for(other.host) as oc:
            stranger = (
                await oc.post(
                    "/api/v1/contacts",
                    json={"first_name": "Vreemde"},
                    headers=await auth_cookie(other.user),
                )
            ).json()
        refused = await c.post(
            "/api/v1/interactions/bulk/assign",
            json={"ids": ids, "contact_ids": [stranger["id"]]},
            headers=member_headers,
        )
        assert refused.status_code == 422, refused.text


async def test_bulk_assign_files_without_approving_and_leaves_unsent_links_alone(
    client_for,
) -> None:
    """Triage first, read later — and an untouched picker must not clear what is stored.

    A bulk dialog starts blank over rows that disagree, so "absent" has to mean *leave alone*.
    The opposite reading would wipe the contact the matcher found on every row in the batch.
    """
    t = await make_tenant("bulk-assign")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-assign.example")
        member_headers = await auth_cookie(member)
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=owner_headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Jan", "last_name": "Jansen"},
                headers=owner_headers,
            )
        ).json()
        row_id = await _seed_gmail_row(
            t, member.id, message_id="m1", thread_id="t1", mappings={"contact_id": contact["id"]}
        )

        result = await c.post(
            "/api/v1/interactions/bulk/assign",
            json={"ids": [row_id], "company_id": company["id"]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 1

        row = (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()
        assert row["company_id"] == company["id"]
        assert row["contact_id"] == contact["id"]  # untouched, not cleared
        assert row["status"] == "pending"  # filing is not approving


async def test_bulk_reject_removes_rows_and_emits_one_suppression_each(client_for) -> None:
    rejected: list[dict] = []
    subscribe("interaction.rejected", _collect(rejected))

    t = await make_tenant("bulk-reject")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-reject.example")
        member_headers = await auth_cookie(member)
        ids = [
            await _seed_gmail_row(t, member.id, message_id=f"m{i}", thread_id=f"t{i}")
            for i in range(3)
        ]

        result = await c.post(
            "/api/v1/interactions/bulk/reject",
            json={"ids": ids, "suppress_thread": True},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 3
        assert len(rejected) == 3
        assert all(event["suppress_thread"] is True for event in rejected)
        for row_id in ids:
            assert (
                await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)
            ).status_code == 404


async def test_a_full_page_of_emails_can_be_selected_and_rejected_in_one_batch(
    client_for,
) -> None:
    """The cap must cover a whole page, because "select all → Afwijzen" is the flow.

    Two numbers have to agree and neither is visible from the other's file: the pager offers
    200 rows (`PAGE_SIZES`, and `coercePageSize` clamps to it on the promise that every list
    route serves it), so the list must *return* 200 and the bulk route must *accept* 200. This
    route capped its `limit` at 100 and its `ids` at 100, which failed at both ends — the list
    answered 422 and the load rendered it as an empty screen, and a selection larger than the
    cap 422'd the whole batch with a red `errors.validation` naming no row.

    One test for both because they are one gesture. It seeds 200 so a regression in either
    number fails here rather than in a browser.
    """
    t = await make_tenant("bulk-fullpage")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-fullpage.example")
        member_headers = await auth_cookie(member)
        ids = [
            await _seed_gmail_row(t, member.id, message_id=f"m{i}", thread_id=f"t{i}")
            for i in range(200)
        ]

        listed = await c.get(
            "/api/v1/interactions", params={"limit": 200, "mine": True}, headers=member_headers
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 200

        result = await c.post(
            "/api/v1/interactions/bulk/reject",
            json={"ids": ids},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["succeeded"] == 200


async def test_one_bad_row_is_reported_not_raised_and_the_rest_still_commit(client_for) -> None:
    """The partial-failure contract.

    Raising mid-batch would roll the whole request back (``require_context`` rolls back on any
    exception), so one row a colleague already reviewed in another tab would silently undo
    every good row beside it. Each is reported instead, with the key the single endpoint raises.
    """
    t = await make_tenant("bulk-partial")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-partial.example")
        member_headers = await auth_cookie(member)
        other = await _member(c, owner_headers, "colleague@bulk-partial.example")

        good = await _seed_gmail_row(t, member.id, message_id="m1", thread_id="t1")
        already = await _seed_gmail_row(t, member.id, message_id="m2", thread_id="t2")
        await c.post(f"/api/v1/interactions/{already}/approve", headers=member_headers)
        someone_elses = await _seed_gmail_row(t, other.id, message_id="m3", thread_id="t3")
        manual = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "call", "occurred_at": _NOW.isoformat(), "subject": "Belletje"},
                headers=member_headers,
            )
        ).json()["id"]
        gone = str(uuid.uuid4())

        result = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": [good, already, someone_elses, manual, gone]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["succeeded"] == 1
        by_id = {item["id"]: item["error"] for item in payload["failed"]}
        assert by_id[already] == "errors.interactions_not_pending"
        assert by_id[someone_elses] == "errors.interactions_owner_only"
        assert by_id[manual] == "errors.interactions_manual_no_review"
        assert by_id[gone] == "errors.not_found"

        # The good row really committed — the failures did not take it down with them.
        assert (await c.get(f"/api/v1/interactions/{good}", headers=member_headers)).json()[
            "status"
        ] == "logged"
        # …and the colleague's row is untouched, not merely unreported.
        other_headers = await auth_cookie(other)
        assert (await c.get(f"/api/v1/interactions/{someone_elses}", headers=other_headers)).json()[
            "status"
        ] == "pending"


async def test_a_bad_link_in_the_payload_is_a_422_for_the_whole_call(client_for) -> None:
    """A row-level problem is reported; a *payload* problem is refused.

    A ``company_id`` that does not exist would fail every row identically, so answering "0
    succeeded, 40 failed" would bury the one thing the caller got wrong.
    """
    t = await make_tenant("bulk-badlink")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-badlink.example")
        member_headers = await auth_cookie(member)
        row_id = await _seed_gmail_row(t, member.id)

        result = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": [row_id], "company_id": str(uuid.uuid4())},
            headers=member_headers,
        )
        assert result.status_code == 422, result.text
        # Nothing was approved on the way to refusing.
        assert (await c.get(f"/api/v1/interactions/{row_id}", headers=member_headers)).json()[
            "status"
        ] == "pending"


async def test_bulk_review_cannot_reach_another_tenants_rows(client_for) -> None:
    """Golden Rule 1, on a surface where the ids come from the caller rather than a filter."""
    a = await make_tenant("bulk-iso-a")
    b = await make_tenant("bulk-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        member_a = await _member(ca, a_headers, "mailbox@bulk-iso-a.example")
        row_a = await _seed_gmail_row(a, member_a.id)
    async with client_for(b.host) as cb:
        member_b = await _member(cb, b_headers, "mailbox@bulk-iso-b.example")
        member_b_headers = await auth_cookie(member_b)
        # Tenant B names tenant A's row by id: absent, exactly as a single get would answer.
        result = await cb.post(
            "/api/v1/interactions/bulk/approve", json={"ids": [row_a]}, headers=member_b_headers
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 0,
            "failed": [{"id": row_a, "error": "errors.not_found"}],
        }
    async with client_for(a.host) as ca:
        member_a_headers = await auth_cookie(member_a)
        assert (await ca.get(f"/api/v1/interactions/{row_a}", headers=member_a_headers)).json()[
            "status"
        ] == "pending"


async def test_bulk_approve_loads_the_whole_selection_in_one_query(
    client_for, count_queries
) -> None:
    """The shape a functional test cannot see (docs/PERFORMANCE.md).

    Approving n rows is n writes, so the *total* cannot be constant and pretending otherwise
    would only pin a number nobody can keep. What must not scale is the **read**: the batch is
    one ``IN`` over the selection, never a ``get_or_404`` per id. That is the one place a bulk
    endpoint silently becomes n round-trips before it has done any work at all.
    """
    t = await make_tenant("bulk-budget")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-budget.example")
        member_headers = await auth_cookie(member)
        ids = [
            await _seed_gmail_row(t, member.id, message_id=f"m{i}", thread_id=f"t{i}")
            for i in range(10)
        ]

        with count_queries() as counter:
            result = await c.post(
                "/api/v1/interactions/bulk/approve", json={"ids": ids}, headers=member_headers
            )
        assert result.json()["succeeded"] == 10
        loads = [
            statement
            for statement in counter.matching("from interactions")
            if "IN (" in statement.upper() and "interactions.id" in statement
        ]
        assert len(loads) == 1, "\n\n".join(loads)
        # And the response is counts, not ten presented rows: no batched label lookup over
        # companies/projects/tasks/contacts is paid for output the caller never reads.
        assert counter.matching("from companies") == []


async def test_duplicate_ids_are_collapsed_not_approved_twice(client_for) -> None:
    """A row checked twice (a re-render, a doubled post) is one approval, not a 409 against
    itself."""
    approved: list[dict] = []
    subscribe("interaction.approved", _collect(approved))

    t = await make_tenant("bulk-dupes")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "mailbox@bulk-dupes.example")
        member_headers = await auth_cookie(member)
        row_id = await _seed_gmail_row(t, member.id)

        result = await c.post(
            "/api/v1/interactions/bulk/approve",
            json={"ids": [row_id, row_id, row_id]},
            headers=member_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 1, "failed": []}
        assert len(approved) == 1


async def test_the_batch_is_bounded(client_for) -> None:
    t = await make_tenant("bulk-cap")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        assert (
            await c.post(
                "/api/v1/interactions/bulk/approve",
                json={"ids": [str(uuid.uuid4()) for _ in range(101)]},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await c.post("/api/v1/interactions/bulk/approve", json={"ids": []}, headers=headers)
        ).status_code == 422
