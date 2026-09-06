"""microsoft.calendar: delta sync state machine, agenda feed, webhook auth, the mirror outbox."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.events import SystemContext, emit
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.calendar import busy as busy_mod
from app.integrations.microsoft.calendar import jobs as jobs_mod
from app.integrations.microsoft.calendar import push as push_mod
from app.integrations.microsoft.calendar.models import (
    MicrosoftCalendarChannel,
    MicrosoftCalendarEvent,
    MicrosoftCalendarEventLink,
)
from app.integrations.microsoft.calendar.push import handle_leave_gone, push_link
from app.integrations.microsoft.calendar.service import (
    REBASELINE_WHEN_LEFT_DAYS,
    ensure_subscription,
    parse_graph_datetime,
    sync_connection,
)
from app.integrations.microsoft.models import MicrosoftConnection, MicrosoftSettings
from app.integrations.microsoft.oauth import SCOPE_CALENDAR
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
    """Scripted Graph: each GET/POST/PATCH/DELETE pops the next queued response."""

    def __init__(self, script: list[tuple[str, _StubResponse]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _pop(self, method: str, url: str, **kwargs) -> _StubResponse:
        self.calls.append((method, url, kwargs.get("params") or kwargs.get("json")))
        assert self.script, f"unexpected Graph call: {method} {url}"
        expected_method, response = self.script.pop(0)
        assert expected_method == method, f"expected {expected_method}, got {method} {url}"
        return response

    async def get(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> _StubResponse:
        return await self._pop("DELETE", url, **kwargs)


def _stub_acting_as(stub: _StubClient):
    @asynccontextmanager
    async def _factory(session, org, connection):  # noqa: ANN001, ARG001
        yield stub

    return _factory


SERVICE_ACTING_AS = "app.integrations.microsoft.calendar.service.acting_as"
PUSH_ACTING_AS = "app.integrations.microsoft.calendar.push.acting_as"


async def _seed(
    tenant, *, calendar_enabled: bool = True, user_id=None, email: str = "me@agency.nl"
) -> uuid.UUID:
    """A microsoft_settings row + an active connection with the calendar scope."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        if user_id is None:
            session.add(MicrosoftSettings(org_id=tenant.org.id, calendar_enabled=calendar_enabled))
        connection = MicrosoftConnection(
            org_id=tenant.org.id,
            user_id=user_id or tenant.user.id,
            microsoft_oid="oid-1",
            email=email,
            scopes=["offline_access", "User.Read", SCOPE_CALENDAR],
            refresh_token_encrypted=encrypt("rt"),
        )
        session.add(connection)
        await session.commit()
        return connection.id


def _event_item(event_id: str, day: str, *, subject: str = "Standup", **extra) -> dict:
    return {
        "id": event_id,
        "subject": subject,
        "isAllDay": False,
        "isCancelled": False,
        "showAs": "busy",
        "webLink": f"https://outlook.office.com/calendar/item/{event_id}",
        "changeKey": "ck-1",
        "lastModifiedDateTime": f"{day}T08:00:00.0000000Z",
        "start": {"dateTime": f"{day}T09:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": f"{day}T09:30:00.0000000", "timeZone": "UTC"},
        "type": "singleInstance",
        **extra,
    }


def _allday_item(event_id: str, start: str, end_exclusive: str, subject: str) -> dict:
    return {
        "id": event_id,
        "subject": subject,
        "isAllDay": True,
        "isCancelled": False,
        "showAs": "free",
        "start": {"dateTime": f"{start}T00:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": f"{end_exclusive}T00:00:00.0000000", "timeZone": "UTC"},
        "type": "singleInstance",
    }


def _delta_response(
    items: list[dict], *, delta_link: str | None = None, next_link: str | None = None
):
    body: dict = {"value": items}
    if next_link:
        body["@odata.nextLink"] = next_link
    if delta_link:
        body["@odata.deltaLink"] = delta_link
    return _StubResponse(200, body)


async def _events(org_id):
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return (await session.execute(select(MicrosoftCalendarEvent))).scalars().all()


async def _run_sync(tenant, connection_id, stub, monkeypatch) -> None:
    monkeypatch.setattr(SERVICE_ACTING_AS, _stub_acting_as(stub))
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        await sync_connection(session, tenant.org, connection)
        await session.commit()


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


def test_graph_datetime_parses_seven_fractional_digits_as_utc() -> None:
    parsed = parse_graph_datetime("2026-07-08T09:00:00.0000000")
    assert parsed == datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    assert parse_graph_datetime("2026-07-08T09:00:00Z") == datetime(2026, 7, 8, 9, tzinfo=UTC)
    assert parse_graph_datetime("2026-07-08T11:00:00+02:00") == datetime(2026, 7, 8, 9, tzinfo=UTC)
    assert parse_graph_datetime(None) is None


async def test_sync_initial_delta_incremental_and_state_lost(monkeypatch) -> None:
    t = await make_tenant("mscal-sync")
    connection_id = await _seed(t)

    # Initial: two pages, a timed and an all-day event, a deltaLink at the end.
    stub = _StubClient(
        [
            (
                "GET",
                _delta_response(
                    [_event_item("ev-1", "2026-07-08")],
                    next_link="https://graph.example/next-1",
                ),
            ),
            (
                "GET",
                _delta_response(
                    [_allday_item("ev-allday", "2026-07-09", "2026-07-11", "Conferentie")],
                    delta_link="https://graph.example/delta-1",
                ),
            ),
        ]
    )
    await _run_sync(t, connection_id, stub, monkeypatch)
    # The first call named the window; the continuation was followed verbatim.
    assert stub.calls[0][1].endswith("/me/calendarView/delta")
    assert "startDateTime" in (stub.calls[0][2] or {}) and "$select" in (stub.calls[0][2] or {})
    assert stub.calls[1][1] == "https://graph.example/next-1" and stub.calls[1][2] is None

    events = await _events(t.org.id)
    by_id = {e.graph_event_id: e for e in events}
    assert set(by_id) == {"ev-1", "ev-allday"}
    assert by_id["ev-1"].start_at == datetime(2026, 7, 8, 9, tzinfo=UTC)
    assert by_id["ev-1"].web_link.startswith("https://outlook.office.com/")
    assert by_id["ev-allday"].all_day and by_id["ev-allday"].start_date == date(2026, 7, 9)
    assert by_id["ev-allday"].end_date == date(2026, 7, 11)  # exclusive, as Graph gives it

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        channel = (await session.execute(select(MicrosoftCalendarChannel))).scalar_one()
        assert channel.delta_link == "https://graph.example/delta-1"
        assert channel.window_end is not None and channel.window_end > date.today()
        assert channel.last_synced_at is not None

    # Incremental: the stored deltaLink is GET verbatim; a tombstone drops, an edit lands.
    stub2 = _StubClient(
        [
            (
                "GET",
                _delta_response(
                    [
                        {"id": "ev-1", "@removed": {"reason": "deleted"}},
                        {
                            **_allday_item("ev-allday", "2026-07-09", "2026-07-11", "Verplaatst"),
                        },
                    ],
                    delta_link="https://graph.example/delta-2",
                ),
            ),
        ]
    )
    await _run_sync(t, connection_id, stub2, monkeypatch)
    assert stub2.calls[0][1] == "https://graph.example/delta-1" and stub2.calls[0][2] is None
    events = await _events(t.org.id)
    assert [(e.graph_event_id, e.subject) for e in events] == [("ev-allday", "Verplaatst")]

    # State lost (410): reset the cursor, wipe the cache, refill once from a fresh window.
    stub3 = _StubClient(
        [
            ("GET", _StubResponse(410)),
            (
                "GET",
                _delta_response(
                    [_event_item("ev-new", "2026-07-20")],
                    delta_link="https://graph.example/delta-3",
                ),
            ),
        ]
    )
    await _run_sync(t, connection_id, stub3, monkeypatch)
    assert "startDateTime" in (stub3.calls[1][2] or {})
    events = await _events(t.org.id)
    assert [e.graph_event_id for e in events] == ["ev-new"]

    # The same reset when Graph names the cause in the body instead of the status.
    stub4 = _StubClient(
        [
            ("GET", _StubResponse(400, {"error": {"code": "SyncStateNotFound", "message": "x"}})),
            ("GET", _delta_response([], delta_link="https://graph.example/delta-4")),
        ]
    )
    await _run_sync(t, connection_id, stub4, monkeypatch)
    assert await _events(t.org.id) == []


async def test_sync_rebaselines_when_the_window_is_nearly_spent(monkeypatch) -> None:
    """Graph fixes the delta window on the first call and never widens it, so a chain must be
    restarted before the Agenda runs out of future — a delta, not a refill, would silently keep
    the cache blind to next quarter."""
    t = await make_tenant("mscal-window")
    connection_id = await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            MicrosoftCalendarChannel(
                org_id=t.org.id,
                connection_id=connection_id,
                calendar_id="primary",
                delta_link="https://graph.example/old-delta",
                window_end=date.today() + timedelta(days=REBASELINE_WHEN_LEFT_DAYS - 1),
            )
        )
        session.add(
            MicrosoftCalendarEvent(
                org_id=t.org.id,
                connection_id=connection_id,
                graph_event_id="stale",
                subject="Stale",
                all_day=True,
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 2),
            )
        )
        await session.commit()

    stub = _StubClient(
        [
            (
                "GET",
                _delta_response(
                    [_event_item("fresh", "2026-08-01")], delta_link="https://graph.example/d"
                ),
            )
        ]
    )
    await _run_sync(t, connection_id, stub, monkeypatch)
    # A fresh window rather than the stored link, and the stale cache is gone with it.
    assert stub.calls[0][1].endswith("/me/calendarView/delta")
    assert "endDateTime" in (stub.calls[0][2] or {})
    assert [e.graph_event_id for e in await _events(t.org.id)] == ["fresh"]
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        channel = (await session.execute(select(MicrosoftCalendarChannel))).scalar_one()
        assert channel.window_end >= date.today() + timedelta(days=300)


async def test_series_master_is_skipped_and_occurrences_name_it(monkeypatch) -> None:
    t = await make_tenant("mscal-series")
    connection_id = await _seed(t)
    stub = _StubClient(
        [
            (
                "GET",
                _delta_response(
                    [
                        {
                            **_event_item("master", "2026-07-06", subject="Weekly"),
                            "type": "seriesMaster",
                        },
                        _event_item(
                            "occ-1",
                            "2026-07-06",
                            subject="Weekly",
                            type="occurrence",
                            seriesMasterId="master",
                        ),
                        _event_item(
                            "occ-2",
                            "2026-07-13",
                            subject="Weekly",
                            type="exception",
                            seriesMasterId="master",
                        ),
                        {**_event_item("declined", "2026-07-14"), "isCancelled": True},
                        {**_event_item("maybe", "2026-07-15"), "showAs": "tentative"},
                    ],
                    delta_link="https://graph.example/d",
                ),
            )
        ]
    )
    await _run_sync(t, connection_id, stub, monkeypatch)
    events = {e.graph_event_id: e for e in await _events(t.org.id)}
    assert set(events) == {"occ-1", "occ-2", "declined", "maybe"}
    assert events["occ-1"].series_master_id == "master"
    assert events["declined"].status == "cancelled"
    assert events["maybe"].status == "tentative"


# --------------------------------------------------------------------------- #
# The Agenda feed
# --------------------------------------------------------------------------- #


async def _cache_event(org_id, connection_id, event_id, *, day: str, subject: str, **extra) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        session.add(
            MicrosoftCalendarEvent(
                org_id=org_id,
                connection_id=connection_id,
                graph_event_id=event_id,
                subject=subject,
                all_day=extra.pop("all_day", False),
                start_at=None
                if extra.get("start_date")
                else datetime.fromisoformat(f"{day}T09:00+00:00"),
                end_at=None
                if extra.get("start_date")
                else datetime.fromisoformat(f"{day}T10:00+00:00"),
                web_link=f"https://outlook.office.com/{event_id}",
                **extra,
            )
        )
        await session.commit()


async def test_events_feed_windows_statuses_and_hides_pushed_mirrors(client_for) -> None:
    t = await make_tenant("mscal-feed")
    connection_id = await _seed(t)
    await _cache_event(t.org.id, connection_id, "in-window", day="2026-07-08", subject="Standup")
    await _cache_event(t.org.id, connection_id, "out-of-window", day="2026-08-08", subject="Later")
    await _cache_event(
        t.org.id,
        connection_id,
        "declined",
        day="2026-07-09",
        subject="Cancelled",
        status="cancelled",
    )
    await _cache_event(
        t.org.id, connection_id, "maybe", day="2026-07-10", subject="Maybe", status="tentative"
    )
    await _cache_event(
        t.org.id,
        connection_id,
        "allday",
        day="2026-07-11",
        subject="Conferentie",
        all_day=True,
        start_date=date(2026, 7, 11),
        end_date=date(2026, 7, 13),
    )
    # A pushed mirror, and an occurrence of a pushed series: both are the same item twice.
    await _cache_event(t.org.id, connection_id, "mine-pushed", day="2026-07-12", subject="Verlof")
    await _cache_event(
        t.org.id,
        connection_id,
        "occ-of-mine",
        day="2026-07-13",
        subject="Beschikbaar",
        series_master_id="series-pushed",
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        for event_id in ("mine-pushed", "series-pushed"):
            session.add(
                MicrosoftCalendarEventLink(
                    org_id=t.org.id,
                    local_type="leave_request",
                    local_id=uuid.uuid4(),
                    user_id=t.user.id,
                    connection_id=connection_id,
                    status="pushed",
                    graph_event_id=event_id,
                    payload={},
                )
            )
        await session.commit()

    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        feed = (
            await c.get(
                "/api/v1/microsoft/calendar/events",
                params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
                headers=headers,
            )
        ).json()
    by_title = {item["title"]: item for item in feed}
    assert set(by_title) == {"11:00 Standup", "11:00 Cancelled", "11:00 Maybe", "Conferentie"}
    assert by_title["11:00 Cancelled"]["cancelled"] and by_title["11:00 Maybe"]["tentative"]
    # Graph's exclusive all-day end becomes the Agenda's inclusive one.
    assert by_title["Conferentie"]["start"] == "2026-07-11"
    assert by_title["Conferentie"]["end"] == "2026-07-12"
    assert by_title["11:00 Standup"]["html_link"].startswith("https://outlook.office.com/")
    assert by_title["11:00 Standup"]["starts_at"].startswith("2026-07-08T09:00")


async def test_events_feed_is_the_viewers_own_and_tenant_scoped(client_for) -> None:
    a = await make_tenant("mscal-iso-a")
    b = await make_tenant("mscal-iso-b")
    connection_a = await _seed(a)
    await _cache_event(a.org.id, connection_a, "ev-a", day="2026-07-08", subject="A's meeting")

    async with client_for(b.host) as cb:
        feed = await cb.get(
            "/api/v1/microsoft/calendar/events",
            params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
            headers=await auth_cookie(b.user),
        )
        assert feed.status_code == 200 and feed.json() == []


# --------------------------------------------------------------------------- #
# Calendar selection
# --------------------------------------------------------------------------- #


def _calendar_list_response() -> _StubResponse:
    return _StubResponse(
        200,
        {
            "value": [
                {"id": "cal-primary", "name": "Agenda", "isDefaultCalendar": True, "canEdit": True},
                {"id": "cal-team", "name": "Team", "isDefaultCalendar": False, "canEdit": True},
                {
                    "id": "cal-ro",
                    "name": "Feestdagen",
                    "isDefaultCalendar": False,
                    "canEdit": False,
                },
            ]
        },
    )


async def test_calendar_selection_adds_removes_and_refuses_unknown(client_for, monkeypatch) -> None:
    t = await make_tenant("mscal-select")
    connection_id = await _seed(t)
    headers = await auth_cookie(t.user)

    enqueued: list[str] = []

    async def _fake_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        enqueued.append(function)

    monkeypatch.setattr("app.core.jobs.enqueue", _fake_enqueue)

    class _FakeRedis:
        store: dict[str, str] = {}

        async def get(self, key):  # noqa: ANN001
            return self.store.get(key)

        async def set(self, key, value, ex=None):  # noqa: ANN001, ARG002
            self.store[key] = value

    monkeypatch.setattr("app.core.cache.get_redis", lambda: _FakeRedis())

    async with client_for(t.host) as c:
        monkeypatch.setattr(
            SERVICE_ACTING_AS, _stub_acting_as(_StubClient([("GET", _calendar_list_response())]))
        )
        listed = await c.get("/api/v1/microsoft/calendar/calendars", headers=headers)
        assert listed.status_code == 200, listed.text
        rows = {row["id"]: row for row in listed.json()}
        assert rows["cal-primary"]["selected"] and rows["cal-primary"]["primary"]
        assert not rows["cal-team"]["selected"] and rows["cal-ro"]["access_role"] == "reader"

        monkeypatch.setattr(
            SERVICE_ACTING_AS, _stub_acting_as(_StubClient([("GET", _calendar_list_response())]))
        )
        chosen = await c.put(
            "/api/v1/microsoft/calendar/calendars",
            json={"calendar_ids": ["cal-team"]},
            headers=headers,
        )
        assert chosen.status_code == 200, chosen.text
        assert {row["id"] for row in chosen.json() if row["selected"]} == {
            "cal-primary",
            "cal-team",
        }
        assert "microsoft_calendar_sync_connection" in enqueued

        # The feeds menu's read comes off the database alone.
        channels = (await c.get("/api/v1/microsoft/calendar/channels", headers=headers)).json()
        assert {row["calendar_id"] for row in channels} == {"primary", "cal-team"}
        assert (
            next(row for row in channels if row["calendar_id"] == "cal-team")["summary"] == "Team"
        )

        # An id off somebody else's account is refused.
        monkeypatch.setattr(
            SERVICE_ACTING_AS, _stub_acting_as(_StubClient([("GET", _calendar_list_response())]))
        )
        refused = await c.put(
            "/api/v1/microsoft/calendar/calendars",
            json={"calendar_ids": ["cal-stranger"]},
            headers=headers,
        )
        assert refused.status_code == 422
        assert (
            refused.json()["error"]["fields"]["calendar_ids"] == "errors.microsoft_calendar_unknown"
        )

    # Cached events for the team calendar leave with the deselection; the primary's stay.
    await _cache_event(
        t.org.id, connection_id, "team-ev", day="2026-07-08", subject="Team", calendar_id="cal-team"
    )
    await _cache_event(t.org.id, connection_id, "own-ev", day="2026-07-08", subject="Mine")
    async with client_for(t.host) as c:
        monkeypatch.setattr(
            SERVICE_ACTING_AS, _stub_acting_as(_StubClient([("GET", _calendar_list_response())]))
        )
        dropped = await c.put(
            "/api/v1/microsoft/calendar/calendars", json={"calendar_ids": []}, headers=headers
        )
        assert dropped.status_code == 200
    assert [e.graph_event_id for e in await _events(t.org.id)] == ["own-ev"]


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #


async def test_webhook_handshake_and_client_state(client_for, monkeypatch) -> None:
    t = await make_tenant("mscal-webhook")
    connection_id = await _seed(t)
    state = f"{t.org.id}.{connection_id}.{uuid.uuid4().hex}"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            MicrosoftCalendarChannel(
                org_id=t.org.id,
                connection_id=connection_id,
                calendar_id="primary",
                subscription_id="sub-1",
                client_state=state,
                watch_status="active",
            )
        )
        await session.commit()

    enqueued: list[tuple] = []

    async def _fake_enqueue(function: str, *args, **kwargs) -> None:  # noqa: ARG001
        enqueued.append((function, args))

    monkeypatch.setattr("app.core.jobs.enqueue", _fake_enqueue)

    async with client_for(t.host) as c:
        # Graph validates a new subscription's URL: the token comes back verbatim, as text.
        handshake = await c.post(
            "/api/v1/microsoft/calendar/webhook", params={"validationToken": "abc 123"}
        )
        assert handshake.status_code == 200
        assert handshake.text == "abc 123"
        assert handshake.headers["content-type"].startswith("text/plain")
        assert not enqueued

        # A notification with the right clientState syncs once per connection.
        ok = await c.post(
            "/api/v1/microsoft/calendar/webhook",
            json={
                "value": [
                    {"subscriptionId": "sub-1", "clientState": state, "changeType": "updated"},
                    {"subscriptionId": "sub-1", "clientState": state, "changeType": "created"},
                ]
            },
        )
        assert ok.status_code == 202
        assert [e[0] for e in enqueued] == ["microsoft_calendar_sync_connection"]
        assert enqueued[0][1] == (str(t.org.id), str(connection_id))

        # A wrong secret, or garbage, is swallowed with the same 202 and nothing queued.
        enqueued.clear()
        wrong = f"{t.org.id}.{connection_id}.{uuid.uuid4().hex}"
        assert (
            await c.post(
                "/api/v1/microsoft/calendar/webhook",
                json={"value": [{"clientState": wrong}]},
            )
        ).status_code == 202
        assert (
            await c.post(
                "/api/v1/microsoft/calendar/webhook",
                json={"value": [{"clientState": "not-a-state"}]},
            )
        ).status_code == 202
        assert (
            await c.post("/api/v1/microsoft/calendar/webhook", content=b"not json")
        ).status_code == 202
        assert not enqueued


# --------------------------------------------------------------------------- #
# The mirror outbox
# --------------------------------------------------------------------------- #


async def test_leave_approved_pushes_and_cancellation_deletes(monkeypatch) -> None:
    t = await make_tenant("mscal-leave")
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
        "breakdown": [{"date": "2026-11-02", "hours": 8}, {"date": "2026-11-03", "hours": 8}],
        "_recipients": [t.user.id],
    }
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await emit("leave.approved", SystemContext(org=t.org, session=session), payload)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        link = (await session.execute(select(MicrosoftCalendarEventLink))).scalar_one()
        assert link.status == "pending" and link.local_id == request_id
        assert link.payload["start_date"] == "2026-11-02"
        assert link.payload["summary"]  # the shared snapshot's localized title
        assert offered

        # Worker creates the event: an all-day span with the exclusive next-midnight end, in
        # the default calendar, marked as schakl's.
        stub = _StubClient([("POST", _StubResponse(201, {"id": "gev-1", "changeKey": "ck-1"}))])
        monkeypatch.setattr(PUSH_ACTING_AS, _stub_acting_as(stub))
        await push_link(session, t.org, link)
        await session.commit()
        assert link.status == "pushed" and link.graph_event_id == "gev-1"
        assert link.change_key == "ck-1"
        method, url, body = stub.calls[0]
        assert (method, url) == ("POST", "/me/events")
        assert body["isAllDay"] is True
        assert body["start"] == {"dateTime": "2026-11-02T00:00:00", "timeZone": "UTC"}
        assert body["end"] == {"dateTime": "2026-11-04T00:00:00", "timeZone": "UTC"}
        assert body["showAs"] == "busy"
        assert "02-11-2026" in body["body"]["content"]
        prop = body["singleValueExtendedProperties"][0]
        assert prop["id"] == push_mod.SCHAKL_PROPERTY_ID
        assert prop["value"] == f"leave_request:{request_id}"

        # An in-place edit re-approves: the stored event is patched, never duplicated.
        await set_current_org(session, t.org.id)
        payload["end_date"] = date(2026, 11, 4)
        await emit("leave.updated", SystemContext(org=t.org, session=session), payload)
        assert link.status == "pending" and link.graph_event_id == "gev-1"
        stub_patch = _StubClient(
            [("PATCH", _StubResponse(200, {"id": "gev-1", "changeKey": "ck-2"}))]
        )
        monkeypatch.setattr(PUSH_ACTING_AS, _stub_acting_as(stub_patch))
        await push_link(session, t.org, link)
        await session.commit()
        assert stub_patch.calls[0][1] == "/me/events/gev-1"
        assert stub_patch.calls[0][2]["end"]["dateTime"] == "2026-11-05T00:00:00"
        assert link.status == "pushed" and link.change_key == "ck-2"

        # Cancellation flips the link to delete_pending; the worker deletes and drops it.
        await set_current_org(session, t.org.id)
        await handle_leave_gone(
            SystemContext(org=t.org, session=session), {"leave_request_id": request_id}
        )
        assert link.status == "delete_pending"
        stub2 = _StubClient([("DELETE", _StubResponse(204))])
        monkeypatch.setattr(PUSH_ACTING_AS, _stub_acting_as(stub2))
        await push_link(session, t.org, link)
        await session.commit()
        assert stub2.calls[0][1] == "/me/events/gev-1"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.execute(select(MicrosoftCalendarEventLink))).first() is None


async def test_leave_approved_skips_unconnected_requester_and_disabled_sync() -> None:
    t = await make_tenant("mscal-noconn")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(MicrosoftSettings(org_id=t.org.id, calendar_enabled=True))
        await session.commit()
    payload = {
        "leave_request_id": uuid.uuid4(),
        "user_id": t.user.id,
        "start_date": date(2026, 11, 2),
        "end_date": date(2026, 11, 2),
    }
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await emit("leave.approved", SystemContext(org=t.org, session=session), payload)
        await session.commit()
        assert (await session.execute(select(MicrosoftCalendarEventLink))).first() is None


async def test_timed_leave_carries_the_org_zone(monkeypatch) -> None:
    t = await make_tenant("mscal-timed")
    await _seed(t)
    monkeypatch.setattr(push_mod, "_enqueue_push", _quiet_offer)
    payload = {
        "leave_request_id": uuid.uuid4(),
        "user_id": t.user.id,
        "start_date": date(2026, 11, 2),
        "end_date": date(2026, 11, 2),
        "start_time": "13:00:00",
        "end_time": "17:00:00",
    }
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await emit("leave.approved", SystemContext(org=t.org, session=session), payload)
        link = (await session.execute(select(MicrosoftCalendarEventLink))).scalar_one()
        body = push_mod._event_body(link.payload)
    assert body["isAllDay"] is False
    assert body["start"] == {"dateTime": "2026-11-02T13:00:00", "timeZone": "Europe/Amsterdam"}
    assert body["end"]["dateTime"] == "2026-11-02T17:00:00"
    assert "recurrence" not in body


async def _quiet_offer(org_id, link_id) -> None:  # noqa: ANN001, ARG001
    return None


async def test_task_block_reassigned_tombstones_the_old_owners_event(monkeypatch) -> None:
    """Whether the *new* owner can receive an event says nothing about the old one's copy."""
    t = await make_tenant("mscal-reassign")
    await _seed(t)
    monkeypatch.setattr(push_mod, "_enqueue_push", _quiet_offer)
    schedule_id = uuid.uuid4()
    base = {
        "schedule_id": schedule_id,
        "task_id": uuid.uuid4(),
        "task_title": "Nieuwsbrief",
        "company_name": "Nova Fietsen",
        "start_date": date(2026, 7, 8),
        "end_date": date(2026, 7, 8),
        "start_time": "09:00:00",
        "end_time": "11:00:00",
    }
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit("task_schedule.saved", ctx, {**base, "user_id": t.user.id})
        link = (await session.execute(select(MicrosoftCalendarEventLink))).scalar_one()
        assert link.payload["summary"] == "Nova Fietsen: Nieuwsbrief"
        stub = _StubClient([("POST", _StubResponse(201, {"id": "gev-block", "changeKey": "c"}))])
        monkeypatch.setattr(PUSH_ACTING_AS, _stub_acting_as(stub))
        await push_link(session, t.org, link)
        assert stub.calls[0][2]["showAs"] == "busy"
        assert stub.calls[0][2]["isAllDay"] is False
        await session.commit()

        # Handed to a colleague who never connected: the old event is tombstoned, the block's
        # own link is dropped (nothing to push to).
        await set_current_org(session, t.org.id)
        await emit("task_schedule.saved", ctx, {**base, "user_id": uuid.uuid4()})
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(MicrosoftCalendarEventLink))).scalars().all()
        assert [(r.status, r.graph_event_id, r.user_id) for r in rows] == [
            ("delete_pending", "gev-block", t.user.id)
        ]


async def test_availability_pushes_a_recurring_free_event(monkeypatch) -> None:
    t = await make_tenant("mscal-avail")
    await _seed(t)
    monkeypatch.setattr(push_mod, "_enqueue_push", _quiet_offer)
    entry_id = uuid.uuid4()
    payload = {
        "availability_id": str(entry_id),
        "user_id": t.user.id,
        "kind": "extra",
        "date": date(2026, 7, 10),  # a Friday
        "start_time": "09:00:00",
        "end_time": "17:00:00",
        "repeat_weeks": 2,
        "repeat_until": "2026-12-18",
        "note": "Ook op vrijdag",
    }
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        await emit("availability.saved", ctx, payload)
        link = (await session.execute(select(MicrosoftCalendarEventLink))).scalar_one()
        body = push_mod._event_body(link.payload)
        assert body["showAs"] == "free"
        assert body["recurrence"] == {
            "pattern": {"type": "weekly", "interval": 2, "daysOfWeek": ["friday"]},
            "range": {"type": "endDate", "startDate": "2026-07-10", "endDate": "2026-12-18"},
        }
        assert body["body"]["content"] == "Ook op vrijdag"

        # An unavailable day is busy, all-day when no window, and open-ended without an until.
        await emit(
            "availability.saved",
            ctx,
            {
                "availability_id": str(uuid.uuid4()),
                "user_id": t.user.id,
                "kind": "unavailable",
                "date": date(2026, 7, 13),
                "repeat_weeks": 1,
            },
        )
        rows = (await session.execute(select(MicrosoftCalendarEventLink))).scalars().all()
        busy_body = push_mod._event_body(next(r for r in rows if r.local_id != entry_id).payload)
        assert busy_body["showAs"] == "busy" and busy_body["isAllDay"] is True
        assert busy_body["recurrence"]["range"] == {"type": "noEnd", "startDate": "2026-07-13"}
        assert busy_body["recurrence"]["pattern"]["daysOfWeek"] == ["monday"]

        # Gone before it was pushed: the link is simply dropped.
        await emit("availability.gone", ctx, {"availability_id": str(entry_id)})
        remaining = (await session.execute(select(MicrosoftCalendarEventLink))).scalars().all()
        assert len(remaining) == 1 and remaining[0].local_id != entry_id


async def test_sweep_tombstones_an_orphaned_task_schedule_link(monkeypatch) -> None:
    t = await make_tenant("mscal-orphan")
    connection_id = await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        for local_type, event_id in (
            ("task_schedule", "gev-orphan"),
            ("leave_request", "gev-leave"),
        ):
            session.add(
                MicrosoftCalendarEventLink(
                    org_id=t.org.id,
                    local_type=local_type,
                    local_id=uuid.uuid4(),
                    user_id=t.user.id,
                    connection_id=connection_id,
                    status="pushed",
                    graph_event_id=event_id,
                    payload={},
                )
            )
        await session.commit()

    offered: list[str] = []

    async def _fake_licensed() -> bool:
        return True

    async def _fake_enqueue(name, *args, **kwargs) -> None:  # noqa: ANN001, ARG001
        offered.append(args[1])

    monkeypatch.setattr(jobs_mod, "_licensed", _fake_licensed)
    monkeypatch.setattr(jobs_mod, "enqueue", _fake_enqueue)
    await jobs_mod.microsoft_calendar_sweep_outbox({})

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(MicrosoftCalendarEventLink))).scalars().all()
        by_event = {row.graph_event_id: row.status for row in rows}
    assert by_event == {"gev-orphan": "delete_pending", "gev-leave": "pushed"}
    assert offered


async def test_push_failures_count_attempts_and_a_missing_connection_fails_cleanly(
    monkeypatch,
) -> None:
    t = await make_tenant("mscal-fail")
    connection_id = await _seed(t)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        link = MicrosoftCalendarEventLink(
            org_id=t.org.id,
            local_type="leave_request",
            local_id=uuid.uuid4(),
            user_id=t.user.id,
            connection_id=connection_id,
            status="pending",
            payload={"summary": "x", "start_date": "2026-07-01", "end_date": "2026-07-01"},
        )
        session.add(link)
        await session.flush()
        monkeypatch.setattr(
            PUSH_ACTING_AS, _stub_acting_as(_StubClient([("POST", _StubResponse(503))]))
        )
        await push_link(session, t.org, link)
        assert link.status == "pending" and link.attempts == 1 and link.last_error

        # A tombstone for a person who since disconnected has nothing to delete with: dropped.
        orphan = MicrosoftCalendarEventLink(
            org_id=t.org.id,
            local_type="leave_request",
            local_id=uuid.uuid4(),
            user_id=None,
            connection_id=None,
            status="delete_pending",
            graph_event_id="gone",
            payload={},
        )
        session.add(orphan)
        await session.flush()
        await push_link(session, t.org, orphan)
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (await session.execute(select(MicrosoftCalendarEventLink))).scalars().all()
        assert [r.status for r in rows] == ["pending"]


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


async def test_subscription_registers_renews_and_parks_on_failure(monkeypatch) -> None:
    t = await make_tenant("mscal-sub")
    connection_id = await _seed(t)
    expiry = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(MicrosoftConnection, connection_id)
        stub = _StubClient(
            [("POST", _StubResponse(201, {"id": "sub-1", "expirationDateTime": expiry}))]
        )
        monkeypatch.setattr(SERVICE_ACTING_AS, _stub_acting_as(stub))
        await ensure_subscription(session, t.org, connection)
        await session.commit()
        await set_current_org(session, t.org.id)  # the GUC is transaction-scoped
        body = stub.calls[0][2]
        assert body["resource"] == "/me/events" and body["changeType"] == "created,updated,deleted"
        assert body["notificationUrl"].endswith("/api/v1/microsoft/calendar/webhook")
        assert body["clientState"].startswith(f"{t.org.id}.{connection_id}.")

        channel = (await session.execute(select(MicrosoftCalendarChannel))).scalar_one()
        assert channel.watch_status == "active" and channel.subscription_id == "sub-1"
        assert channel.client_state == body["clientState"]
        state = channel.client_state

        # Still two days out: nothing to do.
        monkeypatch.setattr(SERVICE_ACTING_AS, _stub_acting_as(_StubClient([])))
        await ensure_subscription(session, t.org, connection)

        # Inside a day: renewed in place, the secret kept (Graph keeps sending it).
        channel.expires_at = datetime.now(UTC) + timedelta(hours=6)
        stub2 = _StubClient(
            [("PATCH", _StubResponse(200, {"id": "sub-1", "expirationDateTime": expiry}))]
        )
        monkeypatch.setattr(SERVICE_ACTING_AS, _stub_acting_as(stub2))
        await ensure_subscription(session, t.org, connection)
        assert stub2.calls[0][1] == "/subscriptions/sub-1"
        assert channel.client_state == state and channel.watch_status == "active"

        # Graph refuses (no public HTTPS): parked on polling, and nothing is lost.
        channel.expires_at = datetime.now(UTC) + timedelta(hours=6)
        stub3 = _StubClient(
            [("PATCH", _StubResponse(400, {"error": {"code": "InvalidRequest", "message": "no"}}))]
        )
        monkeypatch.setattr(SERVICE_ACTING_AS, _stub_acting_as(stub3))
        await ensure_subscription(session, t.org, connection)
        await session.commit()
        assert channel.watch_status == "failed"


# --------------------------------------------------------------------------- #
# Busy provider
# --------------------------------------------------------------------------- #


@dataclass
class _Ctx:
    org: object
    session: object
    user: object
    permissions: set[str]

    def can(self, key: str, scope: str | None = None) -> bool:  # noqa: ARG002
        return key in self.permissions


async def test_busy_provider_names_only_the_callers_own_diary() -> None:
    t = await make_tenant("mscal-busy")
    my_connection = await _seed(t)
    colleague_id = uuid.uuid4()
    async with async_session_maker() as session:
        from app.core.auth.models import User

        session.add(
            User(id=colleague_id, email="col@agency.nl", hashed_password="", is_active=True)
        )
        await session.commit()
    their_connection = await _seed(t, user_id=colleague_id, email="col@agency.nl")
    await _cache_event(t.org.id, my_connection, "mine", day="2026-07-08", subject="Mijn overleg")
    await _cache_event(
        t.org.id, their_connection, "theirs", day="2026-07-08", subject="Hun overleg"
    )
    await _cache_event(
        t.org.id,
        their_connection,
        "declined",
        day="2026-07-08",
        subject="Afgezegd",
        status="cancelled",
    )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = _Ctx(org=t.org, session=session, user=t.user, permissions={"microsoft.calendar.read"})
        items = await busy_mod.microsoft_calendar_busy(
            ctx,  # type: ignore[arg-type]
            [t.user.id, colleague_id],
            datetime(2026, 7, 8, tzinfo=UTC),
            datetime(2026, 7, 9, tzinfo=UTC),
        )
    by_user = {item.user_id: item for item in items}
    assert set(by_user) == {t.user.id, colleague_id}
    assert by_user[t.user.id].title == "Mijn overleg" and by_user[t.user.id].href
    assert by_user[colleague_id].title is None and by_user[colleague_id].ref is None
    assert all(item.source == "microsoft.calendar" for item in items)
