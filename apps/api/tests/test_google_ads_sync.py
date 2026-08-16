"""The nightly mirror: the upserts that make a re-pull safe, and the trend it pays for.

The whole point of storing daily rows is that a comparison stops being a second Google call. So
what is asserted here is the two properties that make that safe — a re-run overwrites rather
than duplicates, and one broken account does not stop the others — plus the one that makes it
worth doing: the trend reads without touching Google at all.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.periods import ComparePeriod
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.integrations.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsChange,
    GoogleAdsDimension,
    GoogleAdsMetricDaily,
)
from app.integrations.google_ads.sync import SyncContext, sync_account
from app.integrations.google_ads.trends import read_trend
from tests.conftest import make_tenant
from tests.googleads_fake import failure, metrics
from tests.test_google_ads_reads import CUSTOMER, _linked, fake  # noqa: F401 — transport fixture

pytestmark = pytest.mark.asyncio


def _daily(day: date, *, cost_micros: int = 0, clicks: int = 0, **extra) -> dict:
    return {"segments": {"date": day.isoformat(), **extra}, "metrics": metrics(
        cost_micros=cost_micros, clicks=clicks
    )}


def _campaign_daily(day: date, campaign_id: str, name: str, *, cost_micros: int) -> dict:
    return {
        "segments": {"date": day.isoformat()},
        "campaign": {"id": campaign_id, "name": name},
        "metrics": metrics(cost_micros=cost_micros),
    }


async def _org_and_account(slug: str):
    t, account_id = await _linked(slug)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        # Detach the row's identity from the session that loaded it.
        _ = account.customer_id
    return t, account_id


def _script_week(fake, base: date, *, cost_micros: int = 10_000_000) -> None:  # noqa: F811
    days = [base - timedelta(days=n) for n in range(7)]
    fake.script("FROM customer", [_daily(d, cost_micros=cost_micros, clicks=5) for d in days])
    fake.script(
        "FROM campaign",
        [_campaign_daily(d, "77", "Merk", cost_micros=cost_micros) for d in days],
    )


async def test_a_second_run_overwrites_rather_than_duplicating(fake) -> None:  # noqa: F811
    """The window is re-pulled every night, because Ads conversions keep arriving for days after
    the click. Without an upsert keyed on what a row *is*, a week of overlap is a week of
    duplicates and every total is wrong by a multiple nobody notices."""
    t, account_id = await _org_and_account("gads-sync-upsert")
    yesterday = date.today() - timedelta(days=1)
    _script_week(fake, yesterday)

    async def run() -> None:
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            account = await session.scalar(
                select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
            )
            assert await sync_account(session, t.org, account)
            await session.commit()

    await run()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        first = await session.scalar(
            select(func.count()).select_from(GoogleAdsMetricDaily)
        )
    await run()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        second = await session.scalar(
            select(func.count()).select_from(GoogleAdsMetricDaily)
        )
    assert first > 0
    assert second == first, f"a re-run duplicated rows: {first} → {second}"


async def test_a_re_run_carries_a_late_conversion_into_the_day_it_belongs_to(fake) -> None:  # noqa: F811
    """The reason the window overlaps at all: a day read once is a day read too early."""
    t, account_id = await _org_and_account("gads-sync-late")
    yesterday = date.today() - timedelta(days=1)
    _script_week(fake, yesterday, cost_micros=10_000_000)

    async def run() -> None:
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            account = await session.scalar(
                select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
            )
            await sync_account(session, t.org, account)
            await session.commit()

    await run()
    # Google now reports more for the same days — a late-attributed conversion.
    fake._scripts.clear()
    _script_week(fake, yesterday, cost_micros=25_000_000)
    await run()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(GoogleAdsMetricDaily).where(
                GoogleAdsMetricDaily.dimension == GoogleAdsDimension.ACCOUNT.value,
                GoogleAdsMetricDaily.date == yesterday,
            )
        )
    assert row.metrics["cost"] == 25.0


async def test_one_broken_account_does_not_stop_the_others(fake) -> None:  # noqa: F811
    """A sync that raised would end the loop, and nineteen working accounts would go unsynced
    because of one revoked grant."""
    t, first_id = await _org_and_account("gads-sync-resilient")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        first = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == first_id)
        )
        second = GoogleAdsAccount(
            org_id=t.org.id,
            customer_id="9998887777",
            login_customer_id=first.login_customer_id,
            connection_id=first.connection_id,
            descriptive_name="Tweede",
            currency_code="EUR",
            time_zone="Europe/Amsterdam",
        )
        session.add(second)
        await session.commit()
        second_id = second.id

    yesterday = date.today() - timedelta(days=1)
    _script_week(fake, yesterday)

    from app.integrations.google_ads.jobs import _sync_org

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # The *first* account refuses; the second must still be mirrored.
        fake._scripts.insert(
            0,
            (
                "FROM customer",
                failure("authorizationError", "USER_PERMISSION_DENIED", status=403),
            ),
        )
        await _sync_org(t.org, session)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = await session.scalars(select(GoogleAdsAccount))
        by_id = {row.id: row for row in rows}
    # Both were attempted; both recorded what happened. Neither raised.
    assert by_id[first_id].last_sync_error is not None
    assert by_id[second_id].last_sync_error is not None
    assert "USER_PERMISSION_DENIED" in by_id[first_id].last_sync_error


async def test_a_change_event_is_mirrored_once_however_often_it_is_read(fake) -> None:  # noqa: F811
    """Google gives change events no id, so the row is identified by what it *is*. Re-mirroring
    an overlapping window must not append the same edit every night."""
    t, account_id = await _org_and_account("gads-sync-changes")
    yesterday = date.today() - timedelta(days=1)
    _script_week(fake, yesterday)
    fake.script(
        "FROM change_event",
        [
            {
                "changeEvent": {
                    "changeDateTime": f"{yesterday.isoformat()} 09:00:00",
                    "changeResourceType": "CAMPAIGN_BUDGET",
                    "resourceChangeOperation": "UPDATE",
                    "changeResourceName": "customers/1/campaignBudgets/9",
                    "changedFields": "amount_micros",
                    "userEmail": "stan@breik.nl",
                    "oldResource": {"campaignBudget": {"amountMicros": "40000000"}},
                    "newResource": {"campaignBudget": {"amountMicros": "400000000"}},
                }
            }
        ],
    )

    for _ in range(2):
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            account = await session.scalar(
                select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
            )
            await sync_account(session, t.org, account)
            await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = list((await session.scalars(select(GoogleAdsChange))).all())
    assert len(rows) == 1
    assert rows[0].changed_by == "stan@breik.nl"
    # Stored as an instant, resolved from the account's own zone, so two accounts in two
    # countries sort against each other.
    assert rows[0].changed_at.tzinfo is not None
    assert rows[0].changed_fields[0]["to"] == "400000000"


async def test_an_event_without_an_identity_is_skipped_not_re_inserted(fake) -> None:  # noqa: F811
    """A row that cannot be identified would be appended on every single run, forever."""
    t, account_id = await _org_and_account("gads-sync-anon")
    yesterday = date.today() - timedelta(days=1)
    _script_week(fake, yesterday)
    fake.script(
        "FROM change_event",
        [{"changeEvent": {"changeResourceType": "CAMPAIGN", "resourceChangeOperation": "UPDATE"}}],
    )
    for _ in range(2):
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            account = await session.scalar(
                select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
            )
            await sync_account(session, t.org, account)
            await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        count = await session.scalar(select(func.count()).select_from(GoogleAdsChange))
    assert count == 0


# --- the trend the mirror pays for ------------------------------------------------------------ #


async def _seed_days(org_id, account_id, days: list[tuple[date, float]]) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        for day, cost in days:
            session.add(
                GoogleAdsMetricDaily(
                    org_id=org_id,
                    account_id=account_id,
                    date=day,
                    dimension=GoogleAdsDimension.ACCOUNT.value,
                    dim_key="",
                    metrics={"cost": cost, "clicks": 10, "impressions": 100, "conversions": 1.0},
                    currency="EUR",
                )
            )
        await session.commit()


async def test_the_trend_makes_no_google_call_at_all(client_for, fake) -> None:  # noqa: F811
    """Which is the whole reason the daily rows are stored. A live comparison would be two Ads
    calls per client per page load for figures that stopped changing weeks ago."""
    from tests.conftest import auth_cookie

    t, account_id = await _org_and_account("gads-trend-offline")
    headers = await auth_cookie(t.user)
    today = date.today()
    start = today - timedelta(days=30)
    await _seed_days(t.org.id, account_id, [(start + timedelta(days=n), 10.0) for n in range(30)])
    fake.calls.clear()
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/trend?period=30d", headers=headers
        )
    assert res.status_code == 200
    assert fake.queries() == [], "the trend read called Google"


async def test_the_trend_names_the_span_it_compared_against(client_for, fake) -> None:  # noqa: F811
    """A percentage is a claim about two spans, and one of them is usually left unsaid (#312)."""
    from tests.conftest import auth_cookie

    t, account_id = await _org_and_account("gads-trend-span")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/trend?period=30d", headers=headers
        )
    body = res.json()
    assert body["compare_mode"] == "year"
    current_from = date.fromisoformat(body["period"]["date_from"])
    compared_from = date.fromisoformat(body["compared_with"]["date_from"])
    # A year earlier, not the preceding month — the comparison seasonality survives.
    assert 360 <= (current_from - compared_from).days <= 370


async def test_a_gap_in_the_series_is_reported_not_smoothed(client_for, fake) -> None:  # noqa: F811
    """A chart with a silent hole reads as a day with no spend, which is a different claim from
    a day nobody has synced."""
    from tests.conftest import auth_cookie

    t, account_id = await _org_and_account("gads-trend-gap")
    headers = await auth_cookie(t.user)
    today = date.today()
    # Five of the last thirty days only.
    await _seed_days(
        t.org.id, account_id, [(today - timedelta(days=n), 10.0) for n in range(2, 7)]
    )
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/trend?period=30d", headers=headers
        )
    body = res.json()
    assert body["missing_days"] > 0
    assert "google_ads.warning.days_not_synced" in body["warnings"]


async def test_a_rotated_encryption_key_stops_one_account_not_the_run(fake) -> None:  # noqa: F811
    """``AdsError`` **is** an ``AppError`` — which is what lets every route surface a Google
    refusal without catching anything, and also means an ordinary ``AppError`` reaches the sync.

    One does: a rotated ``SCHAKL_ENCRYPTION_KEY`` makes the stored developer token unreadable
    and the service answers 409. Uncaught, that single org-wide condition would escape the loop
    and leave every *later* account unsynced with nothing on any row to say why.
    """
    from app.integrations.google_ads.models import GoogleAdsSettings

    t, account_id = await _org_and_account("gads-sync-rotated")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(GoogleAdsSettings).where(GoogleAdsSettings.org_id == t.org.id)
        )
        # Ciphertext this instance's key cannot read — what a key rotation leaves behind.
        row.developer_token_encrypted = "gAAAAABn-not-decryptable-with-this-key"
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        ok = await sync_account(session, t.org, account)
        await session.commit()
    assert ok is False

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
    assert account.last_sync_error == "errors.google_ads_token_unreadable"


async def test_the_trend_reads_two_bounded_windows_never_their_hull(fake) -> None:  # noqa: F811
    """#312: `BETWEEN` the earliest and latest date of a year-over-year comparison drags eleven
    unread months through the session."""
    t, account_id = await _org_and_account("gads-trend-hull")
    today = date.today()
    start, end = today - timedelta(days=30), today - timedelta(days=1)
    # A row squarely between the two windows: it must not be read.
    middle = start - timedelta(days=180)
    await _seed_days(t.org.id, account_id, [(start, 10.0), (middle, 999.0)])
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = RequestContext(
            user=t.user,
            org=t.org,
            session=session,
            permissions=PermissionSet.of(["google_ads.account.read"]),
        )
        result = await read_trend(
            ctx, account_id, start=start, end=end, mode=ComparePeriod.YEAR
        )
    # 999 sits in neither window; a hull read would have summed it into one of them.
    assert result.totals["cost"] == 10.0
    assert result.previous_totals["cost"] == 0.0


async def test_a_delta_against_a_zero_baseline_is_undefined(fake) -> None:  # noqa: F811
    from app.integrations.google_ads.trends import delta

    assert delta(5, 0)["relative"] is None
    assert delta(6, 4)["relative"] == 0.5
    assert delta(None, 4) is None


async def test_a_sync_context_is_enough_for_the_service() -> None:
    """The worker has no request and no user, and the service asks a context only three things."""
    t = await make_tenant("gads-sync-ctx")
    async with async_session_maker() as session:
        ctx = SyncContext(org=t.org, session=session)
        assert ctx.can("anything") is True
        assert ctx.is_portal is False
        assert ctx.company_scope is None
        # `release_db` is a no-op here: a job owns its session outright and has nothing to hand
        # back — committing halfway through would end its own transaction.
        async with ctx.release_db():
            pass
        assert ctx.repo(GoogleAdsAccount) is not None


async def test_the_report_section_costs_one_read_per_account_not_per_campaign(
    fake,  # noqa: F811
    count_queries,
) -> None:
    """The fan-out risk the plan flagged: a section that is one query at three campaigns and
    one-per-campaign at three hundred passes every functional test either way.

    Linear in *accounts* is by design and bounded — a client runs one or two. Linear in
    campaigns or in days would not be, and is what this pins against.
    """
    from sqlalchemy import text as sa_text

    from app.integrations.google_ads.report_sections import GOOGLE_ADS_REPORT_SECTIONS
    from app.registry import ReportWindow

    t_, account_id = await _org_and_account("gads-report-perf")
    today = date.today()
    start_day, end_day = today - timedelta(days=30), today - timedelta(days=1)

    async with async_session_maker() as session:
        await set_current_org(session, t_.org.id)
        row = await session.execute(
            sa_text(
                "INSERT INTO companies (id, org_id, name, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org, 'Klant', 'active', now(), now()) RETURNING id"
            ),
            {"org": str(t_.org.id)},
        )
        company_id = row.scalar_one()
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        account.company_id = company_id
        await session.commit()

    async def seed(campaign_ids: range) -> None:
        async with async_session_maker() as session:
            await set_current_org(session, t_.org.id)
            for n in campaign_ids:
                for offset in range(10):
                    session.add(
                        GoogleAdsMetricDaily(
                            org_id=t_.org.id,
                            account_id=account_id,
                            date=start_day + timedelta(days=offset),
                            dimension=GoogleAdsDimension.CAMPAIGN.value,
                            dim_key=str(n),
                            label=f"Campagne {n}",
                            metrics={"cost": 1.0, "clicks": 1, "conversions": 0.0},
                            currency="EUR",
                        )
                    )
            await session.commit()

    spec = next(s for s in GOOGLE_ADS_REPORT_SECTIONS if s.key == "google_ads.performance")
    window = ReportWindow(
        company_id=company_id,
        start=start_day,
        end=end_day,
        compare_start=None,
        compare_end=None,
    )

    async def cost_of_running() -> int:
        async with async_session_maker() as session:
            await set_current_org(session, t_.org.id)
            ctx = RequestContext(
                user=t_.user,
                org=t_.org,
                session=session,
                permissions=PermissionSet.of(["google_ads.account.read"]),
            )
            with count_queries() as counter:
                assert await spec.provider(ctx, window) is not None
            return len(counter.statements)

    await seed(range(0, 2))
    few = await cost_of_running()
    # Eighteen *more* campaigns, not the same two again — the unique key would refuse that, and
    # rightly: it is the constraint that makes the nightly re-pull an upsert.
    await seed(range(2, 20))
    many = await cost_of_running()

    assert many == few, (
        f"the section fanned out: {few} statements at two campaigns, {many} at twenty"
    )


# --------------------------------------------------------------------------------------- #
# The backfill is a job that is known to have run, not one that was queued once (#381)
# --------------------------------------------------------------------------------------- #
async def test_the_nightly_run_queues_a_fill_that_never_finished(fake, monkeypatch) -> None:  # noqa: F811
    """The promise the link route's own comment already made, finally kept.

    Linking an account queues a thirteen-month fill best-effort, on the stated grounds that "a
    queue miss is not fatal — the nightly run catches up". The nightly run re-pulls a trailing
    week and had no opinion about the year behind it, so a miss was permanent: on the live
    instance not one of thirteen accounts held more than eleven days, and every report for a
    past month printed a Google Ads section of zeros.
    """
    from app.integrations.google_ads import jobs

    t, account_id = await _org_and_account("gads-backfill-queue")
    _script_week(fake, date.today() - timedelta(days=1))
    queued: list[tuple] = []

    async def _capture(function: str, *args, **kwargs):
        queued.append((function, args))
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _capture)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await jobs._sync_org(t.org, session)
        await session.commit()

    assert queued == [("google_ads_backfill_account", (str(t.org.id), str(account_id)))]


async def test_an_account_already_filled_is_not_asked_again(fake, monkeypatch) -> None:  # noqa: F811
    """Otherwise every nightly run re-reads thirteen months for every account, for ever,
    against a shared daily quota."""
    from datetime import UTC, datetime

    from app.integrations.google_ads import jobs

    t, account_id = await _org_and_account("gads-backfill-done")
    _script_week(fake, date.today() - timedelta(days=1))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.get(GoogleAdsAccount, account_id)
        account.backfilled_at = datetime.now(UTC)
        await session.commit()

    queued: list[tuple] = []

    async def _capture(function: str, *args, **kwargs):
        queued.append((function, args))
        return None

    monkeypatch.setattr("app.core.jobs.enqueue", _capture)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await jobs._sync_org(t.org, session)
        await session.commit()

    assert queued == []


async def test_only_a_complete_backfill_stamps_itself(fake, monkeypatch) -> None:  # noqa: F811
    """A halt leaves the column NULL, which is what makes the retry automatic.

    Stamping on entry, or in a `finally`, would turn a backfill that died on a revoked grant
    into an account that is permanently and invisibly short of history — the exact state this
    column exists to end.
    """
    from app.integrations.google_ads import jobs

    t, account_id = await _org_and_account("gads-backfill-halt")
    monkeypatch.setattr(jobs, "_licensed", lambda: _true())
    _script_week(fake, date.today() - timedelta(days=1))

    await jobs.google_ads_backfill_account({}, str(t.org.id), str(account_id))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.get(GoogleAdsAccount, account_id)).backfilled_at is not None

    # Now the same account, refusing on its first chunk.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.get(GoogleAdsAccount, account_id)
        account.backfilled_at = None
        await session.commit()
    fake._scripts.insert(
        0, ("FROM customer", failure("authorizationError", "USER_PERMISSION_DENIED", status=403))
    )

    await jobs.google_ads_backfill_account({}, str(t.org.id), str(account_id))
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert (await session.get(GoogleAdsAccount, account_id)).backfilled_at is None


async def _true() -> bool:
    return True


async def test_a_backfill_chunk_older_than_the_change_log_still_mirrors_its_metrics(  # noqa: F811
    fake,  # noqa: F811
) -> None:
    """The bug that stopped every thirteen-month backfill this feature has ever run (#381).

    `change_event` reaches back thirty days; the metrics reach back four hundred. `read_changes`
    clamped its *start* forward to the earliest Google answers for and left its end where it
    was, so a chunk covering 17 Jun – 16 Jul sent an inverted range and was refused with
    `changeEventError.CHANGE_DATE_RANGE_...`. Sharing one `try` with the three metric reads,
    that refusal discarded them and returned False — and the chunked backfill halts on a False.

    Which is why thirteen accounts on the live instance held exactly thirty days of history each
    while `sync_account` had been reporting success on the first chunk and nothing after it.
    """
    t, account_id = await _org_and_account("gads-backfill-old-chunk")
    old_end = date.today() - timedelta(days=60)
    _script_week(fake, old_end)
    # …and the change log refuses for that window, exactly as Google does.
    fake.script("FROM change_event", failure("changeEventError", "CHANGE_DATE_RANGE_INFINITE"))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        ok = await sync_account(session, t.org, account, days=30, ends_days_ago=59)
        await session.commit()

    assert ok, "a refused change log must not fail the account it rode in on"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        stored = await session.scalar(
            select(func.count()).select_from(GoogleAdsMetricDaily)
        )
    assert stored > 0, "the metrics were read successfully and must still have been written"
