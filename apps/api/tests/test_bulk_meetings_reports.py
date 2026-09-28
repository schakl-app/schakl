"""Bulk delete and the sortable list for meetings and reports (CLAUDE.md §18, §9).

Both are the delete-only shape (``tests/test_bulk.py`` holds the engine's own rules): no import
descriptor, nothing a selection could share, and a register that does collect rows nobody wants.
What is pinned here is what is particular to each — that the row still goes through its own
service, so a meeting a worker is reading and a report a client has already been sent are
*reported*, never removed and never a reason to refuse the rest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select, text

from app.config import settings
from app.core.permissions.deps import iter_route_leaves
from app.core.storage.models import StoredFile
from app.db import async_session_maker, set_current_org
from app.main import app
from app.modules.meetings.models import Meeting, MeetingStatus
from app.modules.reporting.models import Report, ReportAudience
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.test_meetings_api import SETTINGS_BODY, _no_queue, _record  # noqa: F401
from tests.test_reporting import _company as _report_company
from tests.test_reporting import _report


async def _set(org_id: uuid.UUID, model, row_id: str | uuid.UUID, **values) -> None:  # noqa: ANN001, ANN003
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.scalar(select(model).where(model.id == uuid.UUID(str(row_id))))
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()


def test_both_mount_a_delete_route_and_no_update_route() -> None:
    names = {route.name for route in iter_route_leaves(app.routes)}
    for entity in ("meeting", "report"):
        assert f"bulk_delete_{entity}" in names
        assert f"bulk_update_{entity}" not in names


# --- meetings -------------------------------------------------------------------------- #
async def test_a_meeting_selection_deletes_what_it_may_and_reports_the_busy_one(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("bulk-meet")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        first = await _record(c, headers)
        busy = await _record(c, headers)
        last = await _record(c, headers)
        await _set(t.org.id, Meeting, busy["id"], status=MeetingStatus.TRANSCRIBING.value)

        result = await c.post(
            "/api/v1/bulk/meeting/delete",
            json={"ids": [first["id"], busy["id"], last["id"]]},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 2,
            "failed": [{"id": busy["id"], "error": "meetings.error.busy"}],
        }
        listed = (await c.get("/api/v1/meetings", headers=headers)).json()
        assert [row["id"] for row in listed["items"]] == [busy["id"]]

    # Through the service, so the audio went with the rows — and only with those rows.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        files = (
            (await session.execute(select(StoredFile).where(StoredFile.org_id == t.org.id)))
            .scalars()
            .all()
        )
        assert files and {str(f.entity_id) for f in files} == {busy["id"]}


async def test_bulk_meeting_delete_needs_the_delete_key_and_stays_in_the_tenant(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("bulk-meet-rbac")
    other = await make_tenant("bulk-meet-other")
    member = await make_tenant("bulk-meet-m", email="member@bulk-meet.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member.user, org_id=t.org.id)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        meeting = await _record(c, headers)
        # A member reads the register and may not empty it: delete is admin's by default.
        assert (await c.get("/api/v1/meetings", headers=member_headers)).status_code == 200
        refused = await c.post(
            "/api/v1/bulk/meeting/delete", json={"ids": [meeting["id"]]}, headers=member_headers
        )
        assert refused.status_code == 403, refused.text

    async with client_for(other.host) as c:
        foreign = await c.post(
            "/api/v1/bulk/meeting/delete",
            json={"ids": [meeting["id"]]},
            headers=await auth_cookie(other.user),
        )
        assert foreign.status_code == 200, foreign.text
        assert foreign.json() == {
            "succeeded": 0,
            "failed": [{"id": meeting["id"], "error": "errors.not_found"}],
        }
    async with client_for(t.host) as c:
        assert (
            await c.get(f"/api/v1/meetings/{meeting['id']}", headers=headers)
        ).status_code == 200


async def test_meetings_sort_by_an_allowed_key_and_refuse_any_other(
    client_for, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("meet-sort")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/ai/settings", json=SETTINGS_BODY, headers=headers)
        rows = [await _record(c, headers, chunks=1) for _ in range(3)]
        for row, (title, seconds) in zip(
            rows, [("beta", 300), ("Alpha", 900), ("gamma", 60)], strict=True
        ):
            await _set(t.org.id, Meeting, row["id"], title=title, duration_seconds=seconds)

        async def titles(sort: str) -> list[str]:
            res = await c.get(f"/api/v1/meetings?sort={sort}", headers=headers)
            assert res.status_code == 200, res.text
            return [row["title"] for row in res.json()["items"]]

        # Case-blind, the way the column reads.
        assert await titles("title") == ["Alpha", "beta", "gamma"]
        assert await titles("-duration") == ["Alpha", "beta", "gamma"]
        assert await titles("duration") == ["gamma", "beta", "Alpha"]
        refused = await c.get("/api/v1/meetings?sort=transcript_text", headers=headers)
        assert refused.status_code == 400
        assert refused.json()["error"]["message"] == "errors.invalid_sort"


# --- reports --------------------------------------------------------------------------- #
async def test_a_report_selection_deletes_the_unsent_and_reports_the_sent(client_for) -> None:
    t = await make_tenant("bulk-rep")
    headers = await auth_cookie(t.user)
    company = await _report_company(t.org.id)
    draft = await _report(t.org.id, company, published=False, period=date(2026, 5, 1))
    sent = await _report(t.org.id, company, period=date(2026, 6, 1))
    other = await _report(t.org.id, company, published=False, period=date(2026, 7, 1))
    await _set(t.org.id, Report, sent, sent_at=datetime.now(UTC))

    async with client_for(t.host) as c:
        result = await c.post(
            "/api/v1/bulk/report/delete",
            json={"ids": [str(draft), str(sent), str(other)]},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {
            "succeeded": 2,
            "failed": [{"id": str(sent), "error": "errors.reporting.already_sent"}],
        }
        listed = (await c.get("/api/v1/reporting/reports", headers=headers)).json()
        assert [row["id"] for row in listed["items"]] == [str(sent)]


async def test_bulk_report_delete_never_reaches_an_internal_report_the_caller_cannot_read(
    client_for,
) -> None:
    """The audience rule rides along because the row goes through ``ReportService.delete``:
    an internal analysis a caller may not read is *not found*, not refused — saying it exists
    is the leak (§15)."""
    t = await make_tenant("bulk-rep-int")
    other = await make_tenant("bulk-rep-other")
    headers = await auth_cookie(t.user)
    company = await _report_company(t.org.id)
    internal = await _report(
        t.org.id, company, audience=ReportAudience.INTERNAL.value, published=False
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text("DELETE FROM role_permissions WHERE permission = 'reporting.internal.read'")
        )
        await session.commit()
    admin = await make_tenant("bulk-rep-adm", email="admin@bulk-rep.example")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, admin.user.id, role="admin")
        await session.commit()
    admin_headers = await auth_cookie(admin.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        hidden = await c.post(
            "/api/v1/bulk/report/delete", json={"ids": [str(internal)]}, headers=admin_headers
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json() == {
            "succeeded": 0,
            "failed": [{"id": str(internal), "error": "errors.not_found"}],
        }
        # The owner (``*``) still reads it: it was kept, not quietly removed.
        assert (
            await c.get(f"/api/v1/reporting/reports/{internal}", headers=headers)
        ).status_code == 200

    async with client_for(other.host) as c:
        foreign = await c.post(
            "/api/v1/bulk/report/delete",
            json={"ids": [str(internal)]},
            headers=await auth_cookie(other.user),
        )
        assert foreign.json()["succeeded"] == 0


async def test_reports_filter_by_a_status_set_and_sort_with_a_count_that_agrees(
    client_for,
) -> None:
    t = await make_tenant("rep-sort")
    headers = await auth_cookie(t.user)
    company = await _report_company(t.org.id)
    may = await _report(t.org.id, company, period=date(2026, 5, 1))
    june = await _report(t.org.id, company, period=date(2026, 6, 1))
    july = await _report(t.org.id, company, period=date(2026, 7, 1))
    await _set(t.org.id, Report, june, status="failed", company_name="Zeta")
    await _set(t.org.id, Report, july, status="draft", company_name="beta")

    async with client_for(t.host) as c:

        async def ids(query: str) -> tuple[list[str], int]:
            res = await c.get(f"/api/v1/reporting/reports?{query}", headers=headers)
            assert res.status_code == 200, res.text
            return [row["id"] for row in res.json()["items"]], res.json()["total"]

        # The default stays newest period first, every status.
        assert await ids("") == ([str(july), str(june), str(may)], 3)
        assert await ids("sort=period") == ([str(may), str(june), str(july)], 3)
        assert await ids("sort=company") == ([str(may), str(july), str(june)], 3)
        assert await ids("status=draft,failed") == ([str(july), str(june)], 2)
        assert await ids("status=ready&sort=-company") == ([str(may)], 1)
        refused = await c.get("/api/v1/reporting/reports?sort=data_snapshot", headers=headers)
        assert refused.status_code == 400
