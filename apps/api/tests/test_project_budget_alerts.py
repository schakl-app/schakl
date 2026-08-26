"""The project budget alert mail and the org setting behind it (Instellingen → Projecten).

The in-app threshold notification is covered in ``test_notifications_cron``; these pin the
mail half — sent once per state, escalating at 100%, re-arming when the burn drops back —
and the global setting that drives both halves. Mails are captured at the provider seam
(``app.core.email.service.send_email``), the house pattern (docs/EMAIL.md step 5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.db import async_session_maker, set_current_org
from app.modules.projects.budget_watch import watch_for_org
from app.modules.projects.models import Project
from app.modules.time.models import TimeEntry
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_notifications_cron import _add_project, _log_minutes
from tests.test_notifications_fanout import _events, _member

_BREVO = {
    "provider": "brevo",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "xkeysib-secret-123",
}


async def _with_transport(client_for, tenant: Tenant) -> None:
    """A transport so the send does not short-circuit as not-configured."""
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        ).status_code == 200


def _capture_outbox(monkeypatch) -> list:
    outbox: list = []

    async def _send(provider, config, sender, message):  # noqa: ANN001, ARG001
        outbox.append(message)
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", _send)
    return outbox


async def _run_watch(tenant: Tenant) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        count = await watch_for_org(tenant.org, session)
        await session.commit()
        return count


async def _alerted_for(tenant: Tenant, project_id: uuid.UUID) -> str | None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        return await session.scalar(
            select(Project.budget_alerted_for).where(Project.id == project_id)
        )


async def _clear_time(tenant: Tenant, project_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(
            delete(TimeEntry).where(
                TimeEntry.org_id == tenant.org.id, TimeEntry.project_id == project_id
            )
        )
        await session.commit()


async def test_alert_mail_sent_once_per_state_and_escalates(client_for, monkeypatch) -> None:
    t = await make_tenant("budget-mail")
    member = await _member(t, "pm@example.com")
    project_id = await _add_project(t, member.id, budget_hours=10)
    await _with_transport(client_for, t)
    outbox = _capture_outbox(monkeypatch)

    # 80% crosses the default 75 — one mail, to the assignee, carrying the evidence.
    await _log_minutes(t, member.id, project_id, 480)
    await _run_watch(t)
    assert len(outbox) == 1
    message = outbox[0]
    assert message.to == "pm@example.com"
    assert "Rebuild" in message.subject
    assert "80%" in message.text and "80%" in message.html
    assert f"/projects/{project_id}" in message.text, "the link must be in the plaintext part"
    assert await _alerted_for(t, project_id) == "warn:75:total"

    # The same state is not news twice.
    await _run_watch(t)
    assert len(outbox) == 1

    # 110% is a different state: it escalates, once.
    await _log_minutes(t, member.id, project_id, 180)
    await _run_watch(t)
    await _run_watch(t)
    assert len(outbox) == 2
    assert outbox[1].subject != message.subject
    assert "110%" in outbox[1].text
    assert await _alerted_for(t, project_id) == "over:75:total"


async def test_dropping_under_the_threshold_rearms_the_alert(client_for, monkeypatch) -> None:
    t = await make_tenant("budget-mail-rearm")
    member = await _member(t, "rearm@example.com")
    project_id = await _add_project(t, member.id, budget_hours=10)
    await _with_transport(client_for, t)
    outbox = _capture_outbox(monkeypatch)

    await _log_minutes(t, member.id, project_id, 480)
    await _run_watch(t)
    assert len(outbox) == 1

    # The entries are corrected away: the fingerprint clears and nothing new is sent…
    await _clear_time(t, project_id)
    await _run_watch(t)
    assert len(outbox) == 1
    assert await _alerted_for(t, project_id) is None

    # …so crossing again is news again.
    await _log_minutes(t, member.id, project_id, 480)
    await _run_watch(t)
    assert len(outbox) == 2


async def test_disabled_mails_leave_the_notification_standing(client_for, monkeypatch) -> None:
    t = await make_tenant("budget-mail-off")
    member = await _member(t, "quiet@example.com")
    project_id = await _add_project(t, member.id, budget_hours=10)
    await _with_transport(client_for, t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put(
                "/api/v1/projects/settings",
                json={"budget_alert_emails": False},
                headers=headers,
            )
        ).status_code == 200
    outbox = _capture_outbox(monkeypatch)

    await _log_minutes(t, member.id, project_id, 480)
    await _run_watch(t)
    assert outbox == []
    assert len(await _events(t, "project.budget_threshold")) == 1


async def test_the_threshold_setting_drives_bell_and_mail_alike(client_for, monkeypatch) -> None:
    t = await make_tenant("budget-mail-threshold")
    member = await _member(t, "early@example.com")
    project_id = await _add_project(t, member.id, budget_hours=10)
    await _with_transport(client_for, t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put(
                "/api/v1/projects/settings",
                json={"budget_alert_threshold": 50},
                headers=headers,
            )
        ).status_code == 200
    outbox = _capture_outbox(monkeypatch)

    # 60% would say nothing at the default 75; the org has decided 50 means "almost".
    await _log_minutes(t, member.id, project_id, 360)
    await _run_watch(t)
    assert len(outbox) == 1
    assert await _alerted_for(t, project_id) == "warn:50:total"
    events = await _events(t, "project.budget_threshold")
    assert [e.payload["threshold"] for e in events] == [50]


async def test_settings_endpoint_defaults_rbac_and_isolation(client_for) -> None:
    t = await make_tenant("budget-settings")
    other = await make_tenant("budget-settings-other")
    member = await _member(t, "plain@example.com")

    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # An org that never saved a row gets the defaults.
        response = await c.get("/api/v1/projects/settings", headers=owner_headers)
        assert response.status_code == 200
        assert response.json() == {"budget_alert_emails": True, "budget_alert_threshold": 75}

        # A partial write updates only what it names.
        response = await c.put(
            "/api/v1/projects/settings",
            json={"budget_alert_threshold": 90},
            headers=owner_headers,
        )
        assert response.status_code == 200
        assert response.json() == {"budget_alert_emails": True, "budget_alert_threshold": 90}

        # An ordinary member holds no `projects.settings.manage` (admin-only default).
        member_headers = await auth_cookie(member)
        assert (
            await c.get("/api/v1/projects/settings", headers=member_headers)
        ).status_code == 403
        assert (
            await c.put(
                "/api/v1/projects/settings",
                json={"budget_alert_threshold": 10},
                headers=member_headers,
            )
        ).status_code == 403

        # Out of range is refused, not clamped.
        assert (
            await c.put(
                "/api/v1/projects/settings",
                json={"budget_alert_threshold": 200},
                headers=owner_headers,
            )
        ).status_code == 422

    # Tenant isolation: the neighbour still reads the defaults.
    async with client_for(other.host) as c:
        response = await c.get(
            "/api/v1/projects/settings", headers=await auth_cookie(other.user)
        )
        assert response.status_code == 200
        assert response.json()["budget_alert_threshold"] == 75
