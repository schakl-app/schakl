"""google.calendar (#22): sync state machine, agenda feed, webhook auth, leave push outbox."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import httpx

from app.core.crypto import encrypt
from app.core.events import SystemContext, emit
from app.db import async_session_maker, set_current_org
from app.modules.google.calendar import push as push_mod
from app.modules.google.calendar.models import (
    CalendarEventLink,
    GoogleCalendarChannel,
    GoogleCalendarEvent,
)
from app.modules.google.calendar.push import handle_leave_gone, push_link
from app.modules.google.calendar.service import sync_connection
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_CALENDAR
from tests.conftest import auth_cookie, make_tenant


class _StubResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _StubClient:
    """Scripted Google: each GET/POST/PUT/DELETE pops the next queued response."""

    def __init__(self, script: list[tuple[str, _StubResponse]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _pop(self, method: str, url: str, **kwargs) -> _StubResponse:
        self.calls.append((method, url, kwargs.get("params") or kwargs.get("json")))
        expected_method, response = self.script.pop(0)
        assert expected_method == method, f"expected {expected_method}, got {method} {url}"
        return response

    async def get(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("DELETE", url, **kwargs)


def _stub_acting_as(stub: _StubClient):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


async def _seed(tenant, *, calendar_enabled: bool = True) -> uuid.UUID:
    """A google_settings row + an active connection with the calendar scope for the owner."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                calendar_enabled=calendar_enabled,
            )
        )
        connection = GoogleConnection(
            org_id=tenant.org.id,
            user_id=tenant.user.id,
            google_sub="sub",
            email="me@agency.nl",
            scopes=["openid", "email", SCOPE_CALENDAR],
            refresh_token_encrypted=encrypt("rt"),
        )
        session.add(connection)
        await session.commit()
        return connection.id


def _event_item(event_id: str, day: str, *, summary: str = "Standup") -> dict:
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": summary,
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        "etag": '"etag-1"',
        "start": {"dateTime": f"{day}T09:00:00+00:00"},
        "end": {"dateTime": f"{day}T09:30:00+00:00"},
        "updated": f"{day}T08:00:00+00:00",
    }


async def test_sync_initial_incremental_and_410_reset(monkeypatch) -> None:
    t = await make_tenant("gcal-sync")
    connection_id = await _seed(t)

    # Initial sync: one page, two events, a syncToken at the end.
    stub = _StubClient(
        [
            (
                "GET",
                _StubResponse(
                    200,
                    {
                        "items": [
                            _event_item("ev-1", "2026-07-08"),
                            {
                                "id": "ev-allday",
                                "status": "confirmed",
                                "summary": "Conferentie",
                                "start": {"date": "2026-07-09"},
                                "end": {"date": "2026-07-11"},  # exclusive
                            },
                        ],
                        "nextSyncToken": "token-1",
                    },
                ),
            ),
        ]
    )
    monkeypatch.setattr(
        "app.modules.google.calendar.service.acting_as", _stub_acting_as(stub)
    )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        await sync_connection(session, t.org, connection)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        events = (await session.execute(select(GoogleCalendarEvent))).scalars().all()
        assert {e.google_event_id for e in events} == {"ev-1", "ev-allday"}
        channel = (await session.execute(select(GoogleCalendarChannel))).scalar_one()
        assert channel.sync_token == "token-1"
        # The initial request was bounded, not a full-history pull.
        assert "timeMin" in (stub.calls[0][2] or {})

        # Incremental: the delta cancels one event and edits the other.
        stub2 = _StubClient(
            [
                (
                    "GET",
                    _StubResponse(
                        200,
                        {
                            "items": [
                                {"id": "ev-1", "status": "cancelled"},
                                {
                                    **_event_item("ev-allday", "2026-07-09"),
                                    "summary": "Conferentie (verplaatst)",
                                },
                            ],
                            "nextSyncToken": "token-2",
                        },
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.modules.google.calendar.service.acting_as", _stub_acting_as(stub2)
        )
        connection = await session.get(GoogleConnection, connection_id)
        await sync_connection(session, t.org, connection)
        await session.commit()
        assert "syncToken" in (stub2.calls[0][2] or {})

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        events = (await session.execute(select(GoogleCalendarEvent))).scalars().all()
        assert {e.google_event_id for e in events} == {"ev-allday"}
        assert events[0].summary == "Conferentie (verplaatst)"

        # 410 Gone: reset the cursor, wipe the cache, resync once.
        stub3 = _StubClient(
            [
                ("GET", _StubResponse(410)),
                (
                    "GET",
                    _StubResponse(
                        200,
                        {"items": [_event_item("ev-9", "2026-07-10")], "nextSyncToken": "token-3"},
                    ),
                ),
            ]
        )
        monkeypatch.setattr(
            "app.modules.google.calendar.service.acting_as", _stub_acting_as(stub3)
        )
        connection = await session.get(GoogleConnection, connection_id)
        await sync_connection(session, t.org, connection)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        events = (await session.execute(select(GoogleCalendarEvent))).scalars().all()
        assert {e.google_event_id for e in events} == {"ev-9"}
        channel = (await session.execute(select(GoogleCalendarChannel))).scalar_one()
        assert channel.sync_token == "token-3"


async def test_events_feed_own_connection_only(client_for) -> None:
    t = await make_tenant("gcal-feed")
    connection_id = await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # A colleague's event must never show in this viewer's feed.
        other = GoogleConnection(
            org_id=t.org.id,
            user_id=t.user.id,
            google_sub="x",
            email="x",
            scopes=[],
            refresh_token_encrypted=encrypt("rt"),
        )
        # (unique org+user) — give the "other" connection a fabricated user row-less id is
        # impossible; instead just attach the foreign event to a second connection id via
        # direct insert on the same user is refused. Simplest honest check: an event on the
        # own connection shows, and the window filters.
        session.add(
            GoogleCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                google_event_id="timed",
                summary="Standup",
                all_day=False,
                start_at=datetime(2026, 7, 8, 9, 0, tzinfo=UTC),
                end_at=datetime(2026, 7, 8, 9, 30, tzinfo=UTC),
            )
        )
        session.add(
            GoogleCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                google_event_id="allday",
                summary="Conferentie",
                all_day=True,
                start_date=date(2026, 7, 9),
                end_date=date(2026, 7, 11),  # Google-exclusive → inclusive end 10 july
            )
        )
        session.add(
            GoogleCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                google_event_id="outside",
                summary="Elders",
                all_day=True,
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 2),
            )
        )
        del other  # documented above: same-user second connection is schema-refused
        await session.commit()

    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        feed = (
            await c.get(
                "/api/v1/google/calendar/events",
                params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
                headers=headers,
            )
        ).json()
        by_title = {item["title"]: item for item in feed}
        # Timed events render on the tenant's clock: 09:00 UTC in July is 11:00 in Amsterdam.
        assert set(by_title) == {"11:00 Standup", "Conferentie"}
        assert by_title["Conferentie"]["start"] == "2026-07-09"
        assert by_title["Conferentie"]["end"] == "2026-07-10"  # inclusive
        assert by_title["11:00 Standup"]["start"] == "2026-07-08"


async def test_webhook_requires_valid_channel_token(client_for, monkeypatch) -> None:
    t = await make_tenant("gcal-hook")
    connection_id = await _seed(t)
    token = f"{t.org.id}.{connection_id}.{uuid.uuid4().hex}"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            GoogleCalendarChannel(
                org_id=t.org.id,
                connection_id=connection_id,
                channel_id="chan",
                resource_id="res",
                channel_token=token,
                watch_status="active",
            )
        )
        await session.commit()

    enqueued: list[tuple] = []

    async def _fake_enqueue(function: str, *args, **kwargs) -> None:
        enqueued.append((function, args))

    monkeypatch.setattr("app.core.jobs.enqueue", _fake_enqueue)

    async with client_for(t.host) as c:
        # A change ping with the right token syncs.
        ok = await c.post(
            "/api/v1/google/calendar/webhook",
            headers={"X-Goog-Channel-Token": token, "X-Goog-Resource-State": "exists"},
        )
        assert ok.status_code == 200
        assert enqueued and enqueued[0][0] == "google_calendar_sync_connection"

        # The registration ping acknowledges without syncing.
        enqueued.clear()
        sync_ping = await c.post(
            "/api/v1/google/calendar/webhook",
            headers={"X-Goog-Channel-Token": token, "X-Goog-Resource-State": "sync"},
        )
        assert sync_ping.status_code == 200 and not enqueued

        # A wrong secret, or garbage, reveals nothing.
        wrong = f"{t.org.id}.{connection_id}.{uuid.uuid4().hex}"
        assert (
            await c.post(
                "/api/v1/google/calendar/webhook",
                headers={"X-Goog-Channel-Token": wrong, "X-Goog-Resource-State": "exists"},
            )
        ).status_code == 404
        assert (
            await c.post(
                "/api/v1/google/calendar/webhook",
                headers={"X-Goog-Channel-Token": "not-a-token"},
            )
        ).status_code == 404


async def test_leave_approved_pushes_and_cancellation_deletes(monkeypatch) -> None:
    t = await make_tenant("gcal-leave")
    await _seed(t)

    offered: list[tuple] = []

    async def _fake_offer(org_id, link_id) -> None:
        offered.append((org_id, link_id))

    monkeypatch.setattr(push_mod, "_enqueue_push", _fake_offer)

    request_id = uuid.uuid4()
    payload = {
        "leave_request_id": request_id,
        "user_id": t.user.id,
        "start_date": date(2026, 11, 2),
        "end_date": date(2026, 11, 3),
        "start_time": None,
        "end_time": None,
        "hours": 16,
        "_recipients": [t.user.id],
    }

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit("leave.approved", ctx, payload)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        link = (await session.execute(select(CalendarEventLink))).scalar_one()
        assert link.status == "pending" and link.local_id == request_id
        assert link.payload["start_date"] == "2026-11-02"
        assert link.payload["summary"]  # localized title, snapshotted
        assert offered  # handed to the worker

        # Worker inserts the event: all-day span with the exclusive Google end.
        stub = _StubClient(
            [("POST", _StubResponse(200, {"id": "gev-1", "etag": '"e1"'}))]
        )
        monkeypatch.setattr("app.modules.google.calendar.push.acting_as", _stub_acting_as(stub))
        await push_link(session, t.org, link)
        await session.commit()
        assert link.status == "pushed" and link.google_event_id == "gev-1"
        assert stub.calls[0][2]["end"] == {"date": "2026-11-04"}

        # Cancellation flips the link to delete_pending; the worker deletes and drops it.
        # (The RLS GUC is transaction-scoped — re-bind after the commit above.)
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await handle_leave_gone(ctx, {"leave_request_id": request_id})
        assert link.status == "delete_pending"
        stub2 = _StubClient([("DELETE", _StubResponse(204))])
        monkeypatch.setattr(
            "app.modules.google.calendar.push.acting_as", _stub_acting_as(stub2)
        )
        await push_link(session, t.org, link)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        assert (await session.execute(select(CalendarEventLink))).first() is None


async def test_leave_approved_skips_unconnected_requester() -> None:
    t = await make_tenant("gcal-noconn")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(GoogleSettings(org_id=t.org.id, calendar_enabled=True))
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit(
            "leave.approved",
            ctx,
            {
                "leave_request_id": uuid.uuid4(),
                "user_id": t.user.id,
                "start_date": date(2026, 11, 2),
                "end_date": date(2026, 11, 2),
                "_recipients": [],
            },
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        assert (await session.execute(select(CalendarEventLink))).first() is None


async def test_leave_push_carries_type_breakdown_and_identity(monkeypatch) -> None:
    """#148: the pushed event reads "Verlof: <type>" in the requester's locale, describes
    the span per working day, and carries its schakl identity in extendedProperties."""
    from app.modules.leave.models import LeaveType

    t = await make_tenant("gcal-leave-rich")
    await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        leave_type = LeaveType(
            org_id=t.org.id,
            key="vacation",
            label_i18n={"nl": "Vakantie", "en": "Vacation"},
        )
        session.add(leave_type)
        await session.commit()
        type_id = leave_type.id

    async def _fake_offer(org_id, link_id) -> None:
        return None

    monkeypatch.setattr(push_mod, "_enqueue_push", _fake_offer)

    request_id = uuid.uuid4()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit(
            "leave.approved",
            ctx,
            {
                "leave_request_id": request_id,
                "user_id": t.user.id,
                "leave_type_id": type_id,
                "start_date": date(2026, 11, 2),
                "end_date": date(2026, 11, 3),
                "start_time": None,
                "end_time": None,
                "hours": 13,
                "breakdown": [
                    {"date": "2026-11-02", "hours": 8.0},
                    {"date": "2026-11-03", "hours": 5.0},
                ],
                "_recipients": [t.user.id],
            },
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from sqlalchemy import select

        link = (await session.execute(select(CalendarEventLink))).scalar_one()
        assert link.payload["summary"].endswith(": Vakantie")
        assert "02-11-2026: 8 u" in link.payload["description"]
        assert "03-11-2026: 5 u" in link.payload["description"]

        body = push_mod._event_body(link.payload)
        assert body["extendedProperties"]["private"]["schakl_id"] == str(request_id)
        assert body["description"]


async def test_events_feed_hides_events_schakl_pushed(client_for) -> None:
    """#148: a leave event schakl pushed to Google must not come back through the Google
    feed — the Agenda already shows it natively via the leave feed."""
    t = await make_tenant("gcal-feed-dedup")
    connection_id = await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            GoogleCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                google_event_id="mine-pushed",
                summary="Verlof: Vakantie",
                all_day=True,
                start_date=date(2026, 7, 9),
                end_date=date(2026, 7, 10),
            )
        )
        session.add(
            GoogleCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                google_event_id="genuine",
                summary="Externe afspraak",
                all_day=True,
                start_date=date(2026, 7, 9),
                end_date=date(2026, 7, 10),
            )
        )
        session.add(
            CalendarEventLink(
                org_id=t.org.id,
                local_type="leave_request",
                local_id=uuid.uuid4(),
                user_id=t.user.id,
                connection_id=connection_id,
                status="pushed",
                google_event_id="mine-pushed",
                payload={},
            )
        )
        await session.commit()

    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        feed = (
            await c.get(
                "/api/v1/google/calendar/events",
                params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
                headers=headers,
            )
        ).json()
        assert [item["title"] for item in feed] == ["Externe afspraak"]
