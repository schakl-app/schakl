"""The AI Search overview, read once a month and served ever after (docs/SERANKING.md).

**One read path.** The dashboard, the report and the MCP tool all call :meth:`overview`, which
always answers for *the last complete month on the org's calendar* — and fetches it from SE
Ranking when it is not stored yet. "Always shows last month" is therefore a property of the
read, not of a cron that may or may not have run: a client nobody opened this month costs the
agency nothing, and the first colleague (or the report run) who does open it pays for it once.

**A paid call is claimed in the database before it is made** (docs/PAYMENTS.md's rule, one
integration over). The page streams, two colleagues open it at once, the report worker runs
beside them — and "is it stored yet?" followed by a fetch leaves a window all three enter, at
800 units each. So the row is inserted as ``fetching`` under the unique key first
(``ON CONFLICT DO NOTHING``), or an existing row is taken with a conditional ``UPDATE``;
whoever got the row makes the call, and everybody else is told it is being read.

**The connection is handed back around every vendor call** (``ctx.release_db()``, §11). The
entry commit is also what publishes the claim to the other replica.

**A refusal is remembered, and re-asked on its own clock.** A key without Data API access is
refused identically forty times a day; a plan out of units stays out of units until it renews.
Each refusal is stored with no figures and retried after a delay that fits it, while the
manager's *Vernieuwen* asks again at once — because the person who just pasted a new key should
not have to wait for a backoff written for nobody watching. SE Ranking not having published
the month yet is the one "success" that is retried, weekly: it costs 800 units to find out.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.activity.service import ActivityService
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.modules.companies.models import Company
from app.modules.marketing.aisearch import (
    ENGINE_ALL,
    METRICS,
    REFUSALS,
    STATUS_FETCHING,
    STATUS_OK,
    UNITS_DISCOVER_BRAND,
    UNITS_OVERVIEW,
    AiSearchSettings,
    brand_fits,
    change,
    clean_target,
    diff,
    month_of,
    parse,
    parse_overview,
    previous_month,
    resolve,
)
from app.modules.marketing.aisearch.schemas import (
    AiSearchCompanySettingsWrite,
    AiSearchEngineBlock,
    AiSearchMetric,
    AiSearchOverview,
    AiSearchPoint,
    AiSearchSettingsRead,
    BrandLookup,
    SeRankingCheck,
)
from app.modules.marketing.models import (
    MarketingAiSearchSnapshot,
    MarketingCompanySettings,
    MarketingLink,
    MarketingSettings,
    MarketingSource,
)
from app.modules.marketing.sources.seranking import DataApiRefused, SeRankingAdapter

logger = logging.getLogger("schakl.marketing")

_MANAGE = "marketing.link.manage"
_READ = "marketing.metrics.read"

#: A claim nobody finished — a worker that died mid-call — is taken over after this long.
_CLAIM_TTL = timedelta(minutes=3)
#: How long each kind of answer stands before it is asked again unprompted.
_RETRY: dict[str, timedelta] = {
    "failed": timedelta(hours=1),
    "denied": timedelta(hours=6),
    "insufficient": timedelta(hours=24),
}
#: SE Ranking answered, for a month older than the one asked about. Asking again costs a full
#: read, so it waits a week — by which time a monthly database has usually moved.
_RETRY_LAGGING = timedelta(days=7)
#: How far back stored months are read, for the fallback and the brand already discovered.
_HISTORY_MONTHS = 14

_ADAPTER = SeRankingAdapter()


def _read(resolved: AiSearchSettings) -> AiSearchSettingsRead:
    return AiSearchSettingsRead(**resolved.as_dict(), monthly_units=resolved.monthly_units)


def expected_month(today: date) -> date:
    """The month a read is about: the last complete one."""
    return previous_month(today.replace(day=1))


@dataclass(frozen=True)
class _Request:
    """What is asked of SE Ranking for one client — the snapshot key minus engine and month."""

    target: str
    source: str
    scope: str
    brand: str


def _is_due(row: MarketingAiSearchSnapshot, now: datetime) -> bool:
    age = now - row.fetched_at
    if row.status == STATUS_FETCHING:
        return age > _CLAIM_TTL
    if row.status == STATUS_OK:
        lagging = row.data_month is not None and row.data_month < row.period_month
        return lagging and age > _RETRY_LAGGING
    return age > _RETRY.get(row.status, _RETRY["failed"])


class AiSearchService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        #: Rows claimed by this request that already held a good answer — see ``_fetch``.
        self._held_figures: set[uuid.UUID] = set()
        #: The refusal this request met while a good answer was kept, for the manager's eyes.
        self._notice: str | None = None

    # --- settings ----------------------------------------------------------------------- #
    async def _org_blob(self) -> dict | None:
        return await self.ctx.session.scalar(
            select(MarketingSettings.ai_search).where(MarketingSettings.org_id == self.ctx.org.id)
        )

    async def _company_row(self, company_id: uuid.UUID) -> MarketingCompanySettings | None:
        return await self.ctx.session.scalar(
            select(MarketingCompanySettings).where(
                MarketingCompanySettings.org_id == self.ctx.org.id,
                MarketingCompanySettings.company_id == company_id,
            )
        )

    async def _derived_target(self, company_id: uuid.UUID) -> tuple[str, str | None]:
        """A target for a client who named none: their SE Ranking project's own domain, else
        their first website.

        The project comes first because it is the domain the agency already told SE Ranking
        this client is — the same vendor, asked about the same site. Through the repo, so the
        company horizon rides along (§15); the websites read is the module's existing
        by-table-name pattern, since ``websites`` may not be imported (§6).
        """
        stmt = (
            self.ctx.repo(MarketingLink)
            .scoped_select()
            .where(
                MarketingLink.company_id == company_id,
                MarketingLink.source == MarketingSource.SERANKING.value,
                MarketingLink.active.is_(True),
            )
            .order_by(MarketingLink.created_at)
        )
        for link in (await self.ctx.session.execute(stmt)).scalars():
            # The picker stores the project's own domain as ``config.url``. A link made before
            # it did (or seeded by hand) has only its display name — which SE Ranking titles
            # with the domain more often than not, so it is taken when it plainly *is* one.
            name = str(link.display_name or "").strip()
            looks_like_host = "." in name and " " not in name
            target = clean_target((link.config or {}).get("url")) or (
                clean_target(name) if looks_like_host else ""
            )
            if target:
                return target, "seranking"
        name = await self.ctx.session.scalar(
            text(
                "SELECT d.name FROM websites w JOIN domains d ON d.id = w.domain_id"
                " WHERE w.org_id = :org_id AND d.company_id = :company_id"
                " ORDER BY w.created_at LIMIT 1"
            ),
            {"org_id": self.ctx.org.id, "company_id": company_id},
        )
        target = clean_target(name)
        return (target, "website") if target else ("", None)

    async def save_settings(
        self, company_id: uuid.UUID, payload: AiSearchCompanySettingsWrite
    ) -> AiSearchOverview:
        """Replace this client's diff over the house settings, and answer with the overview.

        Posted whole: a blank field is "volg de standaard" and stops overriding. The overview
        that comes back is read **without** asking SE Ranking — saving a form must not spend
        units as a side effect; the page's own read does that, once, where the reader sees it.
        """
        self.ctx.require(_MANAGE)
        await self.ctx.repo(Company).get_or_404(company_id)
        row = await self._company_row(company_id)
        if row is None:
            row = MarketingCompanySettings(
                org_id=self.ctx.org.id, company_id=company_id, show_key_events=True
            )
            self.ctx.session.add(row)
        before = row.ai_search
        row.ai_search = diff(payload.model_dump(), client=True)
        await self.ctx.session.flush()
        if before != row.ai_search:
            await ActivityService(self.ctx).record(
                "company", company_id, "marketing.ai_search_changed", {}
            )
        return await self.overview(company_id, fetch=False)

    # --- the read ----------------------------------------------------------------------- #
    async def _history(
        self, company_id: uuid.UUID, request_target: str, source: str, scope: str, since: date
    ) -> list[MarketingAiSearchSnapshot]:
        stmt = (
            self.ctx.repo(MarketingAiSearchSnapshot)
            .scoped_select()
            .where(
                MarketingAiSearchSnapshot.company_id == company_id,
                MarketingAiSearchSnapshot.target == request_target,
                MarketingAiSearchSnapshot.source == source,
                MarketingAiSearchSnapshot.scope == scope,
                MarketingAiSearchSnapshot.period_month >= since,
            )
            .order_by(MarketingAiSearchSnapshot.period_month.desc())
        )
        return list((await self.ctx.session.execute(stmt)).scalars())

    async def overview(
        self,
        company_id: uuid.UUID,
        *,
        refresh: bool = False,
        fetch: bool = True,
        month: date | None = None,
    ) -> AiSearchOverview:
        """Last month's overview for one client, fetched first where it is not stored.

        ``refresh`` re-asks SE Ranking now, whatever is stored (a manager's act: it spends
        units). ``fetch=False`` reads what is stored and nothing else. ``month`` names another
        month — used by the report, which prints the month it is *about*; only the last
        complete month can ever be fetched, because SE Ranking's summary has no date parameter.
        """
        self.ctx.require(_READ)
        if refresh:
            self.ctx.require(_MANAGE)
        company = await self.ctx.repo(Company).get_or_404(company_id)
        can_manage = self.ctx.can(_MANAGE) and not self.ctx.is_portal

        from app.modules.marketing.service import resolve_source_label

        org_blob = await self._org_blob()
        company_row = await self._company_row(company_id)
        # The tenant's name for the source rides every answer, the off/no-key ones included:
        # each state's sentence names the source, and the name is the tenant's (#446).
        label = await resolve_source_label(
            self.ctx.session, self.ctx.org.id, "seranking", portal=self.ctx.is_portal
        )
        own = company_row.ai_search if company_row else None
        resolved = resolve(org_blob, own)
        latest = expected_month(await org_today(self.ctx.session, self.ctx.org.id))
        period = month or latest

        target, origin = (resolved.target, "setting") if resolved.target else ("", None)
        # Derived for a manager even while the overview is off: the editor's domain box shows
        # it as its placeholder, so somebody switching this on sees *which* site before saving.
        if not target and (resolved.enabled or can_manage):
            target, origin = await self._derived_target(company_id)
        shown = resolved

        def answer(state: str, **extra: Any) -> AiSearchOverview:
            # "No key", "no domain to derive" and "every block was a refusal" are sentences
            # about the agency's desk, and each names the supplier and a settings screen a
            # client cannot open (#446, §15/#274). `_block` already leaves a refusal out for a
            # portal reader; this is the same rule one level up — to a client there is simply
            # no section, which is what "off" draws.
            if self.ctx.is_portal and (state != "ready" or not extra.get("engines")):
                state, extra = "off", {}
            return AiSearchOverview(
                company_id=company_id,
                state=state,
                period_month=period,
                settings=_read(shown),
                target_origin=origin,
                source_label=label,
                can_manage=can_manage,
                own=own if can_manage else None,
                house=_read(parse(org_blob)) if can_manage else None,
                **extra,
            )

        if not resolved.enabled:
            shown = replace(resolved, target=target)
            return answer("off")
        if not target:
            return answer("no_target")
        shown = replace(resolved, target=target)

        rows = await self._history(
            company_id,
            target,
            resolved.source,
            resolved.scope,
            _months_back(period, _HISTORY_MONTHS),
        )
        discovered = next((list(r.discovered_brands) for r in rows if r.discovered_brands), [])

        units_left: int | None = None
        # Only the last complete month can be fetched, and only where a row is missing or due:
        # the test for "is anything to be asked" comes *before* the brand lookup, or a lookup
        # SE Ranking refuses — which stores nothing — would be repeated on every page view.
        if fetch and period == latest and _wanted(rows, resolved, period, discovered, refresh):
            from app.modules.marketing.service import resolve_seranking_data_key

            key, _own_key = await resolve_seranking_data_key(self.ctx.session, self.ctx.org.id)
            if not key:
                return answer("no_key")
            if not resolved.brand and not discovered:
                discovered = await self._discover(key, target, resolved)
            request = _Request(
                target, resolved.source, resolved.scope, resolved.brand or _first(discovered)
            )
            claimed = await self._claim(company_id, request, resolved.engines, period, refresh)
            if claimed:
                units_left = await self._fetch(key, request, claimed, period, discovered)
                rows = await self._history(
                    company_id,
                    target,
                    resolved.source,
                    resolved.scope,
                    _months_back(period, _HISTORY_MONTHS),
                )
        brand = resolved.brand or _first(discovered)
        mine = [row for row in rows if row.brand == brand]

        blocks = [
            block
            for engine in resolved.engines
            if (block := _block(engine, period, mine, portal=self.ctx.is_portal)) is not None
        ]
        return answer(
            "ready",
            brand=brand,
            brand_origin="setting" if resolved.brand else "discovered" if brand else "vendor",
            brand_fits=bool(resolved.brand)
            or brand_fits(brand, company=company.name, target=target),
            discovered_brands=discovered if can_manage else [],
            engines=blocks,
            units_left=units_left if can_manage else None,
            notice=self._notice if can_manage else None,
        )

    # --- the paid half ------------------------------------------------------------------ #
    async def _discover(self, key: str, target: str, resolved: AiSearchSettings) -> list[str]:
        """The brand SE Ranking attributes to the target — asked once per target, 100 units.

        Soft on failure: the overview's own call is about to report whatever is wrong with the
        credential, in its own words, and a brand lookup that raised would turn one refusal
        into a 500. With nothing discovered the overview is asked with no brand, which lets SE
        Ranking resolve one silently — poorer, stated on the screen, and never a guess of ours.
        """
        from app.modules.marketing.service import org_key_client

        try:
            async with self.ctx.release_db(), org_key_client(key) as client:
                return await _ADAPTER.discover_brand(
                    client, target=target, source=resolved.source, scope=resolved.scope
                )
        except DataApiRefused as exc:
            logger.info("seranking discover-brand refused for %s: %s", target, exc)
        except Exception as exc:  # noqa: BLE001 — a lookup never costs the read
            logger.warning("seranking discover-brand failed for %s: %s", target, exc)
        return []

    async def _claim(
        self,
        company_id: uuid.UUID,
        request: _Request,
        engines: tuple[str, ...],
        period: date,
        force: bool,
    ) -> dict[str, uuid.UUID]:
        """Take the rows this request will fetch — ``{engine: row id}``, possibly empty.

        Insert-or-nothing under the unique key for a row that does not exist; a conditional
        update for one that is due. Both are single statements, so two requests racing here
        get disjoint answers from the database rather than from each other's timing.
        """
        now = datetime.now(UTC)
        claimed: dict[str, uuid.UUID] = {}
        table = MarketingAiSearchSnapshot
        for engine in engines:
            inserted = await self.ctx.session.scalar(
                pg_insert(table)
                .values(
                    id=uuid.uuid4(),
                    org_id=self.ctx.org.id,
                    company_id=company_id,
                    target=request.target,
                    source=request.source,
                    scope=request.scope,
                    engine=engine,
                    brand=request.brand,
                    period_month=period,
                    status=STATUS_FETCHING,
                    fetched_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_marketing_ai_search_snapshot_key")
                .returning(table.id)
            )
            if inserted is not None:
                claimed[engine] = inserted
                continue
            row = await self.ctx.session.scalar(
                select(table).where(
                    table.org_id == self.ctx.org.id,
                    table.company_id == company_id,
                    table.target == request.target,
                    table.source == request.source,
                    table.scope == request.scope,
                    table.engine == engine,
                    table.brand == request.brand,
                    table.period_month == period,
                )
            )
            if row is None:
                continue
            if row.status == STATUS_FETCHING and now - row.fetched_at <= _CLAIM_TTL:
                continue  # somebody is reading it right now, forced or not
            if not force and not _is_due(row, now):
                continue
            # Read before the UPDATE: the ORM synchronises the in-session row with it, so
            # afterwards ``row.status`` already says ``fetching``.
            held_figures = row.status == STATUS_OK
            taken = await self.ctx.session.scalar(
                update(table)
                .where(
                    table.id == row.id,
                    table.status == row.status,
                    table.fetched_at == row.fetched_at,
                )
                .values(status=STATUS_FETCHING, fetched_at=now)
                .returning(table.id)
            )
            if taken is not None:
                claimed[engine] = taken
                if held_figures:
                    self._held_figures.add(taken)
        await self.ctx.session.flush()
        return claimed

    async def _fetch(
        self,
        key: str,
        request: _Request,
        claimed: dict[str, uuid.UUID],
        period: date,
        discovered: list[str],
    ) -> int | None:
        """Ask SE Ranking for every claimed engine, then write what it said. Returns the units
        the plan had left, when SE Ranking told us."""
        from app.modules.marketing.service import org_key_client

        results: dict[str, dict[str, Any] | DataApiRefused] = {}
        units_left: int | None = None
        async with self.ctx.release_db(), org_key_client(key) as client:
            # The one free Data API call, first: it says whether the key reaches the Data API
            # at all and whether the plan can afford what is about to be asked — so an empty
            # plan is reported as one without a refused paid call to prove it. Evidence, never
            # the gate (the Cloudflare rule): if the probe itself fails, the read is still made.
            probe: DataApiRefused | None = None
            try:
                units_left = (await _ADAPTER.data_api_subscription(client)).get("units_left")
            except DataApiRefused as exc:
                probe = exc if exc.kind == "denied" else None
            except Exception as exc:  # noqa: BLE001
                logger.info("seranking subscription probe unreachable: %s", exc)
            budget = units_left
            for engine in claimed:
                if probe is not None:
                    results[engine] = probe
                    continue
                if budget is not None and budget < UNITS_OVERVIEW:
                    results[engine] = DataApiRefused("insufficient", None, "")
                    continue
                try:
                    results[engine] = await _ADAPTER.ai_search_overview(
                        client,
                        target=request.target,
                        source=request.source,
                        scope=request.scope,
                        engine=None if engine == ENGINE_ALL else engine,
                        brand=request.brand or None,
                    )
                    if budget is not None:
                        budget -= UNITS_OVERVIEW
                except DataApiRefused as exc:
                    results[engine] = exc
                except Exception as exc:  # noqa: BLE001 — a timeout is one engine's failure
                    logger.warning("seranking ai-search %s failed: %s", engine, exc)
                    results[engine] = DataApiRefused("failed", None, "")

        now = datetime.now(UTC)
        for engine, row_id in claimed.items():
            row = await self.ctx.session.get(MarketingAiSearchSnapshot, row_id)
            if row is None:
                continue
            row.fetched_at = now
            row.discovered_brands = discovered
            outcome = results.get(engine)
            if isinstance(outcome, dict):
                parsed = parse_overview(outcome, period)
                on_time = row.data_month is not None and row.data_month >= row.period_month
                if (
                    row_id in self._held_figures
                    and on_time
                    and parsed.data_month is not None
                    and parsed.data_month < period
                ):
                    # A re-read that comes back *older* than what is stored (a vendor rollback,
                    # a regional database behind the one read before) never replaces a month
                    # that is already the right one — same rule as a refused re-read below.
                    row.status = STATUS_OK
                    continue
                row.status = STATUS_OK
                row.data_month = parsed.data_month
                row.summary = {
                    "metrics": parsed.summary,
                    "realigned": parsed.realigned,
                    "no_data": parsed.no_data,
                }
                row.time_series = parsed.series
                row.units = UNITS_OVERVIEW
            else:
                kind = outcome.kind if isinstance(outcome, DataApiRefused) else "failed"
                kind = kind if kind in REFUSALS else "failed"
                if row_id in self._held_figures:
                    # A re-read (the manager's *Vernieuwen*, or the weekly retry of a lagging
                    # month) that SE Ranking refuses must not cost the answer already stored:
                    # the row keeps its figures and goes back to ``ok``, with ``fetched_at``
                    # moved on so the retry clock restarts rather than firing on every view.
                    # The refusal is reported on this response instead of written over a month.
                    row.status = STATUS_OK
                    self._notice = kind
                    continue
                row.status = kind
                row.units = 0
        await self.ctx.session.flush()
        if units_left is None:
            return None
        spent = sum(UNITS_OVERVIEW for outcome in results.values() if isinstance(outcome, dict))
        return max(0, units_left - spent)

    # --- the brand, asked for by name ---------------------------------------------------- #
    async def lookup_brand(
        self, company_id: uuid.UUID, *, target: str | None = None, source: str | None = None
    ) -> BrandLookup:
        """Ask SE Ranking which brand it attributes to this client's target — 100 units, on a
        manager's press. For the settings editor: the answer goes into the brand box, where
        somebody who knows the client can see it is right before it is saved."""
        from app.modules.marketing.service import org_key_client, resolve_seranking_data_key

        self.ctx.require(_MANAGE)
        await self.ctx.repo(Company).get_or_404(company_id)
        company_row = await self._company_row(company_id)
        resolved = resolve(await self._org_blob(), company_row.ai_search if company_row else None)
        # What the editor has in its boxes wins over what is stored; both go through the same
        # cleaning a save would apply, so "https://www.klant.nl/" asks about www.klant.nl.
        if source:
            resolved = parse({"source": source}, base=resolved, client=True)
        target = (
            clean_target(target)
            or resolved.target
            or (await self._derived_target(company_id))[0]
        )
        if not target:
            raise AppError(
                "validation_error",
                "errors.marketing_ai_search_no_target",
                status_code=422,
                fields={"target": "errors.marketing_ai_search_no_target"},
            )
        key, _ = await resolve_seranking_data_key(self.ctx.session, self.ctx.org.id)
        if not key:
            raise AppError(
                "not_configured", "marketing.seranking_not_configured", status_code=409
            )
        try:
            async with self.ctx.release_db(), org_key_client(key) as client:
                brands = await _ADAPTER.discover_brand(
                    client, target=target, source=resolved.source, scope=resolved.scope
                )
        except DataApiRefused as exc:
            raise _refused(exc) from exc
        return BrandLookup(target=target, brands=brands, units=UNITS_DISCOVER_BRAND)

    # --- the key check ------------------------------------------------------------------- #
    async def check(self) -> SeRankingCheck:
        """Which of SE Ranking's two APIs the stored key(s) reach, and what the plan has left.

        Free: both probes are calls SE Ranking does not charge for. Each API is asked with the
        key that would actually be used for it, so "Data API: refused" is a sentence about the
        right credential (§10: two credentials, two error paths).
        """
        from app.modules.marketing.service import (
            org_key_client,
            resolve_seranking_data_key,
            resolve_seranking_key,
        )

        self.ctx.require(_MANAGE)
        shared = await resolve_seranking_key(self.ctx.session, self.ctx.org.id)
        data_key, own = await resolve_seranking_data_key(self.ctx.session, self.ctx.org.id)
        house = parse(await self._org_blob())
        clients = await self._enabled_client_count(house)
        out = SeRankingCheck(
            configured=bool(shared or data_key),
            project_api="not_configured",
            data_api="not_configured",
            data_api_key="own" if own else "shared",
            enabled_clients=clients,
            monthly_units=clients * house.monthly_units,
        )
        if not out.configured:
            return out
        async with self.ctx.release_db():
            if shared:
                async with org_key_client(shared) as client:
                    out.project_api = (await _ADAPTER.verify(client))["project_api"]
            if data_key:
                async with org_key_client(data_key) as client:
                    verdict = await _ADAPTER.verify(client)
                out.data_api = verdict["data_api"]
                subscription = verdict.get("subscription") or {}
                out.subscription_status = str(subscription.get("status") or "")
                out.units_limit = subscription.get("units_limit")
                out.units_left = subscription.get("units_left")
                out.expires_at = str(subscription.get("expires_at") or "")
        return out

    async def _enabled_client_count(self, house: AiSearchSettings) -> int:
        """How many clients the settings would read each month — for the cost line.

        Clients who switched it on themselves, plus — where the house default is on — every
        client with an active marketing link who did not switch it off. "Every company" would
        be the wrong denominator: an agency's register holds leads and suppliers nobody
        reports on, and the overview is only ever read from a marketing screen or a report.
        """
        rows = (
            await self.ctx.session.execute(
                select(MarketingCompanySettings.company_id, MarketingCompanySettings.ai_search)
                .where(MarketingCompanySettings.org_id == self.ctx.org.id)
                .where(MarketingCompanySettings.ai_search.is_not(None))
            )
        ).all()
        explicit = {
            company_id: blob.get("enabled")
            for company_id, blob in rows
            if isinstance(blob, dict) and isinstance(blob.get("enabled"), bool)
        }
        on = {company_id for company_id, enabled in explicit.items() if enabled}
        if house.enabled:
            linked = (
                await self.ctx.session.execute(
                    select(MarketingLink.company_id)
                    .where(
                        MarketingLink.org_id == self.ctx.org.id,
                        MarketingLink.active.is_(True),
                    )
                    .distinct()
                )
            ).scalars()
            on |= {company_id for company_id in linked if explicit.get(company_id) is not False}
        return len(on)


# --- shaping ------------------------------------------------------------------------------------ #
def _first(items: list[str]) -> str:
    return items[0] if items else ""


def _wanted(
    rows: list[MarketingAiSearchSnapshot],
    resolved: AiSearchSettings,
    period: date,
    discovered: list[str],
    force: bool,
) -> bool:
    """Whether any configured engine has no answer for ``period`` yet, or one that is due.

    Judged under the brand a read would be keyed on *as far as it is known* — a typed one, else
    the one already discovered, else none. A brand found by the lookup that follows simply makes
    the claim insert fresh rows, which is right: it is a different question.
    """
    if force:
        return True
    brand = resolved.brand or _first(discovered)
    now = datetime.now(UTC)
    for engine in resolved.engines:
        row = next(
            (
                r
                for r in rows
                if r.engine == engine and r.brand == brand and r.period_month == period
            ),
            None,
        )
        if row is None or _is_due(row, now):
            return True
    return False


def _months_back(month: date, count: int) -> date:
    for _ in range(count):
        month = previous_month(month)
    return month


def _refused(exc: DataApiRefused) -> AppError:
    """A Data API refusal as our envelope: our sentence, never the vendor's prose (§9)."""
    key = {
        "denied": "errors.marketing_seranking_data_api_denied",
        "insufficient": "errors.marketing_seranking_units_spent",
    }.get(exc.kind, "errors.marketing_seranking_failed")
    return AppError(
        f"seranking_{exc.kind}",
        key,
        status_code=502 if exc.kind == "failed" else 409,
        details={"seranking_status": exc.status} if exc.status else None,
    )


def _block(
    engine: str,
    period: date,
    rows: list[MarketingAiSearchSnapshot],
    *,
    portal: bool,
) -> AiSearchEngineBlock | None:
    """One engine's block: the asked-about month where it is stored, else the newest stored
    month that has figures — labelled with the month it really is.

    A client never sees a refusal: "the key lacks Data API access" is a sentence about the
    agency's desk (§15, #274), so for a portal reader a block with nothing to show is left out.
    """
    of_engine = [row for row in rows if row.engine == engine]
    current = next((row for row in of_engine if row.period_month == period), None)
    figures = next(
        (row for row in of_engine if row.status == STATUS_OK and row.period_month <= period),
        None,
    )
    status = current.status if current is not None else "missing"
    if figures is None:
        if portal:
            return None
        return AiSearchEngineBlock(
            engine=engine,  # type: ignore[arg-type]
            status=status,
            period_month=period,
            fetched_at=current.fetched_at if current is not None else None,
        )
    stored = figures.summary or {}
    metrics_blob = stored.get("metrics") or {}
    data_month = figures.data_month or figures.period_month
    compare_month = previous_month(data_month)
    # SE Ranking's summary never carries a "previous" (docs/SERANKING.md §10), and two of the
    # four figures have no series to take one from — so the month before is the one *we* stored,
    # where we did. Same brand, target and engine by construction: ``rows`` is already narrowed.
    earlier = next(
        (
            row
            for row in of_engine
            if row.status == STATUS_OK
            and row is not figures
            and (row.data_month or row.period_month) == compare_month
        ),
        None,
    )
    earlier_blob = ((earlier.summary or {}).get("metrics") or {}) if earlier else {}
    metrics: list[AiSearchMetric] = []
    for key in METRICS:
        pair = metrics_blob.get(key) or {}
        previous = pair.get("previous")
        if previous is None:
            previous = (earlier_blob.get(key) or {}).get("current")
        moved = change(key, pair.get("current"), previous)
        metrics.append(
            AiSearchMetric(
                key=key,  # type: ignore[arg-type]
                current=pair.get("current"),
                previous=previous,
                change_absolute=moved["absolute"],
                change_percent=moved["percent"],
                direction=moved["direction"],
                verdict=moved["verdict"],
            )
        )
    no_data = bool(stored.get("no_data"))
    if no_data and portal:
        return None
    return AiSearchEngineBlock(
        engine=engine,  # type: ignore[arg-type]
        # The figures shown are good; what is reported is how the *asked-about* month went, so
        # a manager still learns that this month's read was refused while looking at last
        # month's numbers. A client is simply shown the numbers.
        status=STATUS_OK if portal or figures is current else status,
        period_month=period,
        data_month=data_month,
        compare_month=compare_month,
        metrics=[] if no_data else metrics,
        series={
            stream: [
                AiSearchPoint(month=point["month"], value=point["value"])
                for point in points
                if month_of(point.get("month")) is not None
            ]
            for stream, points in (figures.time_series or {}).items()
        },
        fetched_at=figures.fetched_at,
        realigned=bool(stored.get("realigned")),
        no_data=no_data,
    )
