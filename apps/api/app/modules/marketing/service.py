"""marketing service (epic #134): links, pickers, stored-metric reads, live drill-downs, overview.

Two rules shape everything here:

- **The panel/tab/overview read *our* database only.** Trends, deltas and the cross-client grid
  come from ``marketing_metrics_daily`` — one query per screen, zero Google calls
  (docs/PERFORMANCE.md). Only the pickers and the tier-2 drill-downs touch Google, and those are
  Redis-cached.
- **A period total re-derives averages, never sums them.** Average position / CTR / engagement
  over N days is impression- or session-weighted, not the sum of N daily values.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.activity import ActivityService
from app.core.auth.models import User
from app.core.cache import get_redis
from app.core.crypto import decrypt, encrypt
from app.core.googleads import ads_developer_token, attach_ads_account
from app.core.jobs import enqueue
from app.core.models import OrgSettings
from app.core.narratives import latest_narrative
from app.core.periods import (
    ComparePeriod,
    compare_window,
    period_days,
    resolve_compare,
    resolve_period,
)
from app.core.tagmanager import company_containers
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo

# The credential seam, not the module (§6). ``wordpress`` may be disabled, in which case the
# resolver answers ``None`` — the same answer as "this website has no credential yet", which
# every caller here already handles.
from app.core.wordpress import (
    CONFIGURED_STAGES,
    STAGE_CREDENTIAL_REFUSED,
    STAGE_READY,
    STAGE_SITE_ERROR,
    STAGE_UNREACHABLE,
)
from app.core.wordpress import describe_setup as describe_wordpress_setup
from app.core.wordpress import open_client as open_wordpress_client
from app.core.wordpress import resolve_credential as resolve_wordpress_credential
from app.errors import AppError
from app.i18n import resolve_locale, translate
from app.integrations.google import client as google_client
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.modules.companies.models import Company
from app.modules.marketing.layout import (
    GA4_KEY_EVENT_DRILLDOWN,
    GA4_KEY_EVENT_TILES,
    CompanyLayout,
    resolve_event_label,
    resolved_drilldowns,
    resolved_primary,
    resolved_tiles,
    source_hidden,
    source_layout,
    validate_layout,
)
from app.modules.marketing.models import (
    MarketingCompanySettings,
    MarketingLink,
    MarketingMetricDaily,
    MarketingSettings,
    MarketingSource,
)
from app.modules.marketing.rankings import effective_source
from app.modules.marketing.rankings import (
    parse as parse_rankings,
)
from app.modules.marketing.rankings import (
    resolve as resolve_rankings,
)
from app.modules.marketing.reportsplit import (
    parse as parse_report,
)
from app.modules.marketing.reportsplit import (
    resolve as resolve_report,
)
from app.modules.marketing.schemas import (
    AccountsResponse,
    AvailableAccount,
    CompanyMarketing,
    CompanySettingsRead,
    ConnectionOwner,
    DrilldownResponse,
    DrilldownRowOut,
    KpiValue,
    LinkBrief,
    LinkCreate,
    LinkRead,
    MarketingClientList,
    MarketingClientRow,
    MarketingClientSource,
    MarketingCompareWindow,
    MarketingConnection,
    MarketingSettingsRead,
    MarketingSettingsWrite,
    MarketingSummary,
    MarketingSummaryRow,
    OverviewResponse,
    OverviewRow,
    RankingSettingsRead,
    ReportSplitSettingsRead,
    SeriesData,
    SourceAiVisibility,
    SourceMetrics,
    WebsiteRef,
)
from app.modules.marketing.sources import source_auth, source_for
from app.modules.marketing.sources.base import (
    AUTH_GOOGLE,
    AUTH_SITE_KEY,
    AVERAGED_METRICS,
    LOWER_IS_BETTER,
    METRICS_BY_SOURCE,
)
from app.modules.marketing.sources.gads import AdsNotConfigured, developer_token_scope

logger = logging.getLogger("schakl.marketing")

#: Impression/session-weighted, not summed, when aggregating a period (see module doc).
_WEIGHT_BY_METRIC = {"ctr": "impressions", "position": "impressions", "engagementRate": "sessions"}

#: The picker's option list is cached briefly — the same 10 minutes the Google path uses.
_ACCOUNTS_CACHE_SECONDS = 600

#: The longest trailing window a caller may ask for. A named month or quarter is bounded by the
#: calendar and needs no cap; only ``<n>d`` can be typed into a URL unbounded.
MAX_RANGE_DAYS = 400

#: How loudly a link's state speaks when a client has several links of one source — the worst
#: one is what the chip says (:meth:`MarketingService.linked_clients`).
_CLIENT_STATE_ORDER = {"ok": 0, "pending": 1, "error": 2}


def _org_key_error(exc: Exception, source: str) -> str:
    """Turn an org-key source's HTTP failure into something an admin can act on.

    A rejected key and a bad gateway are different problems with different fixes, and "er ging
    iets mis" sends an admin to the wrong screen. Only the two that are actually diagnosable
    get their own key; everything else keeps the generic one rather than guessing.
    """
    # Two shapes, because two different clients raise here. An org-key source speaks httpx and
    # carries `.response.status_code`; a site-key source's failure is the owning module's own
    # exception and carries `.status`. Reading only the first is why
    # `marketing.rankmath_key_rejected` was in both catalogs and unreachable from anywhere in
    # the codebase (#435) — every WordPress refusal fell through to the generic key.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status in (401, 403):
        return f"marketing.{source}_key_rejected"
    return "marketing.accounts_error"


#: The connect-flow query flag that adds each source's scope (the picker's connect deep-link).
_CONNECT_FLAG = {
    MarketingSource.GA4.value: "include_analytics",
    MarketingSource.GSC.value: "include_search_console",
    MarketingSource.GADS.value: "include_ads",
}

#: The headline metric each overview column reads, and which source feeds it (#133).
_OVERVIEW_COLUMNS: dict[str, tuple[str, str]] = {
    # column key: (source, metric)
    "sessions": (MarketingSource.GA4.value, "sessions"),
    "clicks": (MarketingSource.GSC.value, "clicks"),
    "position": (MarketingSource.GSC.value, "position"),
    "cost": (MarketingSource.GADS.value, "cost"),
    "conversions": (MarketingSource.GA4.value, "conversions"),
}

_DRILLDOWN_TTL = 3600  # ~1h, tier-2 lives behind this (issue #133)

#: The GA4 metrics a client's ``show_key_events=False`` withholds — key events and their
#: display alias. Scoped to GA4: Google Ads keeps its own ``conversions`` (#134).
_GA4_GATED_METRICS = ("keyEvents", "conversions")


def _delta_pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


class CompanyPrefs(NamedTuple):
    """One client's marketing display preferences, as the read paths need them."""

    show_key_events: bool = True
    layout: dict | None = None
    #: The stored comparison override; ``None`` = follow the org default (#312).
    compare: str | None = None


def compare_windows(
    today: date, period: str | None, mode: ComparePeriod
) -> MarketingCompareWindow:
    """The span a screen shows and the span it measures against (#312, #316).

    The current window ends **yesterday**, as it always has: today is partial, and comparing
    fourteen hours against twenty-four reads as a collapse in traffic every morning. What is new
    is that *neither* end is derived from a day count any more — ``period`` names the span
    (``30d``, ``last_month``, ``2026-07``, ``2026-Q3``) and ``core.periods`` resolves it — and the
    comparison is whatever the mode says rather than "the same length, immediately before". Both
    spans travel to the client so the screen can name them.
    """
    cur_start, cur_end = resolve_period(period, today, max_days=MAX_RANGE_DAYS)
    start, end = compare_window(cur_start, cur_end, mode)
    return MarketingCompareWindow(
        mode=mode,
        current_start=cur_start,
        current_end=cur_end,
        start=start,
        end=end,
    )


def period_token(period: str | None, range_days: int | None) -> str | None:
    """One period token from the two ways a caller can ask for a span.

    ``range_days`` predates the period vocabulary (#316) and stays on every endpoint: it is in
    shared URLs, in the MCP tool surface generated from the spec, and in whatever an automation
    already calls. ``period`` wins when both arrive, because it is the more specific request —
    "July" is not a number of days.
    """
    if period:
        return period
    return f"{range_days}d" if range_days else None


def _failure_key(
    detail: google_client.GoogleApiError | None, fallback: str, *, source: str | None = None
) -> str:
    """Which message a live Google failure earns — the two 403s have opposite cures.

    A disabled Cloud API and an under-scoped token are both ``403``: reconnecting fixes the
    second and is a dead end for the first, so a single "try reconnecting" is wrong half the
    time. Anything Google didn't diagnose keeps the caller's own fallback.

    A **404 from Google Ads** is its own case, and only there: the Ads API carries its version
    in the URL and sunsets it about a year after release, so every path under a dead version
    answers 404 with a valid token, an enabled API and a correct account. Nothing in the UI
    described that, and the only trace was a 404 in the container log — so it gets named. (For
    GA4/GSC a 404 means the property or site is gone, which is not the same advice at all.)
    """
    if detail is None:
        return fallback
    if detail.api_disabled:
        return "marketing.api_not_enabled"
    if detail.scope_insufficient:
        return "marketing.scope_insufficient"
    if source == MarketingSource.GADS.value and detail.status_code == 404:
        return "marketing.ads_api_version"
    return fallback


def aggregate(source: str, rows: list[dict[str, Any]]) -> dict[str, float]:
    """Collapse a list of daily ``metrics`` dicts into one period total for ``source``."""
    out: dict[str, float] = {}
    for metric in METRICS_BY_SOURCE.get(source, []):
        if metric in AVERAGED_METRICS:
            weight_key = _WEIGHT_BY_METRIC.get(metric)
            num = 0.0
            den = 0.0
            for row in rows:
                value = float(row.get(metric, 0) or 0)
                weight = float(row.get(weight_key, 0) or 0) if weight_key else 1.0
                num += value * weight
                den += weight
            out[metric] = round(num / den, 4) if den else 0.0
        else:
            out[metric] = round(sum(float(row.get(metric, 0) or 0) for row in rows), 4)
    return out


async def resolve_ads_developer_token(
    session: AsyncSession, org_id: uuid.UUID, *, ctx: Any = None
) -> str | None:
    """The effective Google Ads developer token for ``org_id`` (#134).

    Three sources, in order, and the order is the expand/contract migration made visible:

    1. **The ``google_ads`` module**, through the core seam. That module owns the credential
       now, and its ``google_ads_settings`` row is where an admin rotates it.
    2. **This module's own legacy column**, still read because an install that has not enabled
       ``google_ads`` must keep working exactly as it did. The migration *copied* the value
       rather than moving it, so both answer the same thing on an upgraded box; this branch
       disappears with the column in the contracting release.
    3. The deprecated ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var.

    ``ctx`` is optional because the worker sync calls this with a bare session. Without one the
    seam is skipped and the legacy column answers — which is correct rather than a shortcut: a
    background job on an install without the module has nothing else to ask.
    """
    if ctx is not None:
        try:
            return await ads_developer_token(ctx)
        except AdsNotConfigured:
            # The module is absent, or present and holding no token. Either way the legacy
            # column may still have one, and an upgraded install is precisely that case.
            pass
    row = await session.scalar(select(MarketingSettings).where(MarketingSettings.org_id == org_id))
    if row is not None and row.ads_developer_token_encrypted:
        try:
            return decrypt(row.ads_developer_token_encrypted)
        except ValueError:  # key rotated: the stored token is dead — fall through to the env
            pass
    return settings.google_ads_developer_token or None


async def resolve_seranking_key(session: AsyncSession, org_id: uuid.UUID) -> str | None:
    """The agency's SE Ranking API key for ``org_id`` (#300).

    No env fallback, deliberately: the Ads token has one because it *was* env config before
    #134 and existing installs had to keep working. SE Ranking never was, so introducing an
    environment variable now would create the very thing CLAUDE.md §5 argues against — a
    setting a self-hoster edits in a file rather than in Instellingen.
    """
    row = await session.scalar(
        select(MarketingSettings).where(MarketingSettings.org_id == org_id)
    )
    if row is not None and row.seranking_api_key_encrypted:
        try:
            return decrypt(row.seranking_api_key_encrypted)
        except ValueError:  # key rotated: the stored secret is unreadable, so it is not there
            return None
    return None


class SourceNotConfigured(RuntimeError):
    """No credential exists for this source (yet). Carries the i18n key that says which kind.

    Not an error in the ordinary sense — it is what an install that has not set the source up
    looks like every night, and what the picker must *teach* from rather than 500 on. The key
    differs per auth kind because the fix does: an org-key source sends an admin to
    Instellingen → Marketing, and a site-key source sends them to one client's website page.
    """

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key


@asynccontextmanager
async def keyed_client(
    session: AsyncSession,
    org_id: uuid.UUID,
    source: str,
    website_id: uuid.UUID | None = None,
) -> AsyncIterator[Any]:
    """A prepared HTTP client for whichever credential ``source`` rides.

    The seam #300 predicted would not be needed. Its docstring said a fourth source is "a new
    module here plus one line in ``SOURCES``, no service change", and the one thing that
    prediction missed was authentication — then the *fifth* source missed it the same way, for
    a third kind of credential. Two identical surprises is the point at which per-kind ``if``
    branches at every call site stop being cheaper than one dispatch, so the picker, the
    drill-down and the nightly sync now all ask for a client and this decides where it comes
    from. Raises :class:`SourceNotConfigured` when there is no credential to build one with;
    every caller already had that branch, and now they share its wording.

    Google is deliberately **not** here: its client needs a per-user connection, an incremental
    scope check and a reconnect prompt, none of which is expressible as "hand me a credential".
    That path stays its own, which is exactly what :data:`AUTH_GOOGLE` means.
    """
    if source_auth(source) == AUTH_SITE_KEY:
        if website_id is None:
            # A site-key source with no website has no credential to find, by construction:
            # `create_link` refuses such a link and the picker requires the parameter. This is
            # the "the website was deleted out from under a live link" path.
            raise SourceNotConfigured(f"marketing.{source}_no_website")
        credential = await resolve_wordpress_credential(session, org_id, website_id)
        if credential is None:
            raise SourceNotConfigured(f"marketing.{source}_not_connected")
        # Built by the module that owns the credential, never constructed here (§6). The
        # client opens a short-lived transport per call, so there is nothing to close.
        yield open_wordpress_client(credential)
        return

    key = await resolve_seranking_key(session, org_id)
    if not key:
        raise SourceNotConfigured(f"marketing.{source}_not_configured")
    async with org_key_client(key) as client:
        yield client


@asynccontextmanager
async def org_key_client(api_key: str) -> AsyncIterator[Any]:
    """An HTTP client for an org-key source, with the credential already on it.

    The mirror of ``google_client.acting_as`` for a source that has no OAuth: same shape at the
    call site, so the service's three fetch paths read the same whichever kind of source they
    are serving. Timeouts are generous because a site-audit report is a large document, and
    bounded because a hung request inside a cron would hold a worker slot indefinitely.
    """
    import httpx

    client = httpx.AsyncClient(
        headers={
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )
    try:
        yield client
    finally:
        await client.aclose()


class MarketingService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _resolve_ads_developer_token(self) -> str | None:
        # In a request the context is available, so the ``google_ads`` module's token wins.
        return await resolve_ads_developer_token(
            self.ctx.session, self.ctx.org.id, ctx=self.ctx
        )

    # --- credential-keyed sources (#300, docs/WORDPRESS.md) --------------------------------- #
    async def _keyed_accounts(
        self,
        source: MarketingSource,
        adapter: Any,
        *,
        website_id: uuid.UUID | None = None,
        refresh: bool = False,
    ) -> AccountsResponse:
        """The picker for a source with no OAuth: a credential, configured or not.

        ``connected``/``has_scope`` are reported true because for these kinds of source they
        are not questions — there is no connection to make and no scope to grant. The only
        prerequisite is the credential, which rides ``configured`` exactly as the Ads developer
        token already does, so the web teaches "not configured yet" from a state it can already
        draw.

        ``website_id`` is what a **site-key** source needs and every other source ignores: a
        Rank Math picker lists the brands tracked on *one client's WordPress*, so there is no
        agency-wide list to fetch and the question is meaningless without naming a site.

        For a site-key source every exit also carries a **stage** (#435). ``configured`` alone
        was one boolean over six prerequisites that three different people fix in three
        different products, so "no credential yet" and "the credential was refused" answered
        identically and "Rank Math is not installed on this site" and "this client has no brand
        yet" were both an empty list. The classification is asked of the module that owns the
        credential (``app/core/wordpress.describe_setup``) rather than done here, because
        telling ``rest_no_route`` from ``aiv_unauthorized`` means speaking a vocabulary §6
        forbids this module from importing — and the one time it tried, by duck-typing
        ``exc.response.status_code``, it read an attribute that does not exist.

        ``refresh`` skips the cache **read** and still writes: a brand created in WordPress a
        minute ago must be reachable without waiting out a ten-minute entry, and a list that is
        confidently out of date with no control that disagrees with it is the "not all of them"
        half of the same complaint.
        """
        # Keyed on the website for a site-key source: two clients' brand lists are different
        # answers to the same question and must never share a cache entry.
        scope_key = f":{website_id}" if website_id else ""
        cache_key = (
            f"schakl:marketing:accounts:{self.ctx.org.id}:{source.value}{scope_key}"
        )
        site_key = source_auth(source.value) == AUTH_SITE_KEY

        async def described(
            *,
            configured: bool,
            exc: Exception | None = None,
            brand_count: int | None = None,
        ) -> dict[str, Any]:
            """The stage fields for a site-key source, plus who decides ``configured``.

            For any other kind of source the branch's own answer stands and no stage exists —
            an org key is configured or it is not, and there is no per-website setup to be
            partway through. For a site-key source the stage is the better answer, because it
            is the one that can tell "the credential is fine and this client has no brand yet"
            from "there is no credential".
            """
            if not site_key or website_id is None:
                return {"configured": configured}
            if brand_count:
                # A non-empty list *is* the evidence that every prerequisite is met, so the
                # stage is knowable without a query. This is the common case — every page load
                # of a client that is already set up — and PERFORMANCE.md's rule is that a
                # screen does not pay for a diagnosis it has nothing to diagnose.
                return {"setup_stage": STAGE_READY, "setup_links": {}, "configured": True}
            state = await describe_wordpress_setup(
                self.ctx.session, self.ctx.org.id, website_id, exc=exc, brand_count=brand_count
            )
            return {
                "setup_stage": state.stage,
                "setup_detail": state.detail,
                "setup_links": state.links,
                "configured": state.stage in CONFIGURED_STAGES,
            }

        try:
            # The credential is resolved **before** the cache is consulted, which is what the
            # org-key path always did (it read the key, then Redis) and is worth keeping: an
            # install that has not configured this source must answer `configured=False`
            # without a cache round trip, because that is the answer on every page load
            # forever, not a miss worth caching.
            async with keyed_client(
                self.ctx.session, self.ctx.org.id, source.value, website_id
            ) as client:
                cached = None if refresh else await get_redis().get(cache_key)
                if cached is not None:
                    options = [AvailableAccount(**item) for item in json.loads(cached)]
                    return AccountsResponse(
                        source=source,
                        connected=True,
                        has_scope=True,
                        accounts=options,
                        **await described(configured=True, brand_count=len(options)),
                    )
                # The pool connection is handed back for the live listing, the same rule the
                # Google path follows (docs/PERFORMANCE.md).
                async with self.ctx.release_db():
                    fetched = await adapter.list_accounts(client)
        except SourceNotConfigured:
            # No credential yet. Not an error and not an empty list: `configured=False` is the
            # state the picker teaches from, and it is what an install that has not set this
            # source up looks like on every page load. For a site-key source the stage says
            # *which* kind of nothing this is — no row at all, or a deactivated one.
            return AccountsResponse(
                source=source,
                connected=True,
                has_scope=True,
                **await described(configured=False),
            )
        except Exception as exc:  # noqa: BLE001 — a live fetch failure teaches, never 500s
            logger.warning("marketing %s account listing failed: %s", source.value, exc)
            described_fields = await described(configured=True, exc=exc)
            error = _org_key_error(exc, source.value)
            stage = described_fields.get("setup_stage")
            if stage is not None and stage not in (
                STAGE_CREDENTIAL_REFUSED,
                STAGE_UNREACHABLE,
                STAGE_SITE_ERROR,
            ):
                # A prerequisite nobody has completed yet is a *setup state*, not a failure.
                # Drawing "er ging iets mis" in red over a checklist that names the exact next
                # step is the noise this issue is about: "Rank Math is not installed on this
                # site" is not something that went wrong, it is something still to do.
                error = None
            return AccountsResponse(
                source=source,
                connected=True,
                has_scope=True,
                error=error,
                **described_fields,
            )
        options = [
            AvailableAccount(
                external_id=option.external_id,
                display_name=option.display_name,
                config=option.config,
                account_hint=option.account_hint,
            )
            for option in fetched
        ]
        await get_redis().set(
            cache_key,
            json.dumps([option.model_dump(mode="json") for option in options]),
            ex=_ACCOUNTS_CACHE_SECONDS,
        )
        return AccountsResponse(
            source=source,
            connected=True,
            has_scope=True,
            accounts=options,
            **await described(configured=True, brand_count=len(options)),
        )

    # --- shared helpers ------------------------------------------------------------------- #
    async def _today(self) -> date:
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        return datetime.now(zone).date()

    async def _company_or_404(self, company_id: uuid.UUID) -> Company:
        return await self.ctx.repo(Company).get_or_404(company_id)

    async def _company_websites(self, company_id: uuid.UUID) -> list[WebsiteRef]:
        """The client's websites (id + domain name), for the link pickers and group labels.

        Raw SQL by table name (the websites module's own `_attach` pattern) — modules never
        import each other's internals. A website's display name *is* its domain.
        """
        rows = (
            await self.ctx.session.execute(
                text(
                    "SELECT w.id, d.name FROM websites w"
                    " JOIN domains d ON d.id = w.domain_id"
                    " WHERE w.org_id = :org_id AND d.company_id = :company_id"
                    " ORDER BY d.name"
                ),
                {"org_id": self.ctx.org.id, "company_id": company_id},
            )
        ).all()
        return [WebsiteRef(id=row[0], name=row[1]) for row in rows]

    async def _website_names(self, website_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """{website_id: domain name} for links that carry one — one query, display-only."""
        if not website_ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                text(
                    "SELECT w.id, d.name FROM websites w"
                    " JOIN domains d ON d.id = w.domain_id"
                    " WHERE w.org_id = :org_id AND w.id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"org_id": self.ctx.org.id, "ids": list(website_ids)},
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    async def _links(
        self, *, company_id: uuid.UUID | None = None, include_inactive: bool = False
    ) -> list[MarketingLink]:
        # Through the repo, so the horizon rides along (#285). Both callers pass a
        # ``company_id`` that ``_company_or_404`` already checked, so this changes nothing
        # today — it is what keeps the *next* caller from being a leak.
        stmt = self.ctx.repo(MarketingLink).scoped_select()
        if company_id is not None:
            stmt = stmt.where(MarketingLink.company_id == company_id)
        if not include_inactive:
            stmt = stmt.where(MarketingLink.active.is_(True))
        stmt = stmt.order_by(MarketingLink.source, MarketingLink.display_name)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _connections_by_id(
        self,
    ) -> tuple[dict[uuid.UUID, GoogleConnection], dict[uuid.UUID, ConnectionOwner]]:
        """Every Google connection in the org, and **whose** each one is — one joined query.

        The owner's name is not decoration: a marketing link syncs through one colleague's
        grant, so every screen that renders its numbers should be able to say through whom.
        Joined here rather than resolved per link, so a client with five linked properties
        still costs one statement (docs/PERFORMANCE.md). **Outer** joined, because the owner is
        a display concern and the connection is a sync fact: a link whose owning account has
        gone must still read as connected — an inner join would silently turn it into
        "disconnected" and send someone chasing a grant that is working fine.
        """
        rows = (
            await self.ctx.session.execute(
                select(GoogleConnection, User.full_name, User.email)
                .outerjoin(User, User.id == GoogleConnection.user_id)
                .where(GoogleConnection.org_id == self.ctx.org.id)
            )
        ).all()
        connections: dict[uuid.UUID, GoogleConnection] = {}
        owners: dict[uuid.UUID, ConnectionOwner] = {}
        for connection, full_name, user_email in rows:
            connections[connection.id] = connection
            if user_email is None:
                continue  # the account is gone; the link still syncs, it just names nobody
            owners[connection.id] = ConnectionOwner(
                user_id=connection.user_id,
                # A colleague who never filled in a name is still a person to point at: their
                # login address names them better than an empty string does.
                name=(full_name or "").strip() or user_email,
                email=connection.email,
                is_me=connection.user_id == self.ctx.user.id,
            )
        return connections, owners

    async def _any_connection(self) -> bool:
        return bool(
            await self.ctx.session.scalar(
                select(func.count(GoogleConnection.id)).where(
                    GoogleConnection.org_id == self.ctx.org.id
                )
            )
        )

    async def _settings_map(
        self, company_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, CompanyPrefs]:
        """``{company_id: CompanyPrefs}`` for the given companies — one query.

        A company with **no** settings row falls back to the defaults (key events shown, no
        layout, no comparison override): absence means the pre-existing behaviour, so nothing
        changes until someone edits.
        """
        if not company_ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                select(
                    MarketingCompanySettings.company_id,
                    MarketingCompanySettings.show_key_events,
                    MarketingCompanySettings.layout,
                    MarketingCompanySettings.compare,
                ).where(
                    MarketingCompanySettings.org_id == self.ctx.org.id,
                    MarketingCompanySettings.company_id.in_(company_ids),
                )
            )
        ).all()
        return {
            company_id: CompanyPrefs(bool(flag), layout, compare)
            for company_id, flag, layout, compare in rows
        }

    async def _company_settings(self, company_id: uuid.UUID) -> CompanyPrefs:
        return (await self._settings_map([company_id])).get(company_id, CompanyPrefs())

    async def _show_key_events(self, company_id: uuid.UUID) -> bool:
        return (await self._company_settings(company_id)).show_key_events

    async def _prefs_with_default(
        self, company_id: uuid.UUID
    ) -> tuple[CompanyPrefs, ComparePeriod, dict[str, str]]:
        """This client's preferences, the org default **and** the tenant's source names, in one
        statement (#312).

        Two single-row lookups is the obvious shape and it costs the company hub a query it does
        not need to spend: that page composes a provider per enabled module in sequence, so
        "+1 each" is precisely how it gets slow, and #290's budget exists to catch it. Every row
        is a unique-index lookup, so they ride as scalar subqueries on one FROM-less ``SELECT``,
        which Postgres answers with exactly one row whether or not any row exists — the
        distinction the read needs anyway, since absent means *the defaults* on every side.

        The source names (#446) joined this statement the day they stopped being portal-only:
        read as their own query they were exactly the "+1" above, on the path the hub's panel
        calls, and ``test_the_two_windows_are_read_as_two_windows`` said so.
        """
        org_id = self.ctx.org.id

        def of_company(column: Any) -> Any:
            return (
                select(column)
                .where(
                    MarketingCompanySettings.org_id == org_id,
                    MarketingCompanySettings.company_id == company_id,
                )
                .scalar_subquery()
            )

        def of_org(column: Any) -> Any:
            return select(column).where(MarketingSettings.org_id == org_id).scalar_subquery()

        show_key_events, layout, compare, org_default, stored_labels, locale = (
            await self.ctx.session.execute(
                select(
                    of_company(MarketingCompanySettings.show_key_events),
                    of_company(MarketingCompanySettings.layout),
                    of_company(MarketingCompanySettings.compare),
                    of_org(MarketingSettings.default_compare),
                    of_org(MarketingSettings.portal_source_labels),
                    select(OrgSettings.default_locale)
                    .where(OrgSettings.org_id == org_id)
                    .scalar_subquery(),
                )
            )
        ).one()
        prefs = CompanyPrefs(
            show_key_events=True if show_key_events is None else bool(show_key_events),
            layout=layout,
            compare=compare,
        )
        return prefs, resolve_compare(org_default), _shape_source_labels(stored_labels, locale)

    async def _org_default_compare(self) -> ComparePeriod:
        """The agency's house comparison (#312) — the code default while nothing is stored.

        One scalar read of the org's own settings row, which is why the per-client resolution
        below takes it as an argument: a cross-client grid asks for it once, not once per row.
        """
        stored = await self.ctx.session.scalar(
            select(MarketingSettings.default_compare).where(
                MarketingSettings.org_id == self.ctx.org.id
            )
        )
        return resolve_compare(stored)

    # --- links (#132) --------------------------------------------------------------------- #
    async def company_websites(self, company_id: uuid.UUID) -> list[WebsiteRef]:
        """The client's websites, for the connect dialog's site select (#399).

        Its own endpoint rather than a read of ``/websites``, and gated on
        ``marketing.link.manage`` rather than on ``websites.website.read``: the question being
        asked is "which of this client's sites does the Rank Math link attach to", which is
        part of the link the caller is already allowed to make. Requiring the websites module's
        own read permission would mean the site-key picker refuses for exactly the person the
        agency put in charge of connecting marketing sources (#310, the same shape).
        """
        self.ctx.require("marketing.link.manage")
        await self._company_or_404(company_id)
        return await self._company_websites(company_id)

    async def list_links_read(self, company_id: uuid.UUID) -> list[LinkRead]:
        self.ctx.require("marketing.metrics.read")
        # 404 on another tenant's (or a nonexistent) company, so the list can't be probed for the
        # existence of a company outside the caller's org; an own company with no links is [].
        await self._company_or_404(company_id)
        links = await self._links(company_id=company_id, include_inactive=True)
        connections, owners = await self._connections_by_id()
        website_names = await self._website_names(
            {link.website_id for link in links if link.website_id is not None}
        )
        return [self._link_read(link, connections, website_names, owners) for link in links]

    def _link_read(
        self,
        link: MarketingLink,
        connections: dict[uuid.UUID, GoogleConnection],
        website_names: dict[uuid.UUID, str] | None = None,
        owners: dict[uuid.UUID, ConnectionOwner] | None = None,
    ) -> LinkRead:
        connection = connections.get(link.connection_id) if link.connection_id else None
        return LinkRead(
            id=link.id,
            company_id=link.company_id,
            website_id=link.website_id,
            website_name=(website_names or {}).get(link.website_id) if link.website_id else None,
            source=MarketingSource(link.source),
            external_id=link.external_id,
            display_name=link.display_name,
            config=link.config or {},
            active=link.active,
            last_synced_at=link.last_synced_at,
            last_error=link.last_error,
            backfill_done=link.backfill_done,
            connection_ok=bool(connection and connection.status == ConnectionStatus.ACTIVE.value),
            connection_owner=(owners or {}).get(link.connection_id) if link.connection_id else None,
        )

    async def create_link(self, data: LinkCreate) -> LinkRead:
        self.ctx.require("marketing.link.manage")
        await self._company_or_404(data.company_id)
        # A link may attach to one of *this* client's websites; anything else (another client's
        # site, a stale id) is a 404 — same non-leaking shape as the company check above.
        website_names: dict[uuid.UUID, str] = {}
        if data.website_id is not None:
            websites = {w.id: w.name for w in await self._company_websites(data.company_id)}
            if data.website_id not in websites:
                raise AppError("not_found", "errors.not_found", status_code=404)
            website_names[data.website_id] = websites[data.website_id]
        # A site-key source has no credential to sync with unless a website names one, so the
        # website is required rather than optional for it — and refused up front rather than
        # discovered as a `last_error` on the first nightly run, because a link created here is
        # a link a marketeer expects to see numbers from tomorrow.
        if source_auth(data.source.value) == AUTH_SITE_KEY:
            if data.website_id is None:
                raise AppError(
                    "validation",
                    f"errors.marketing_{data.source.value}_website_required",
                    status_code=422,
                    fields={"website_id": "errors.required"},
                )
            if (
                await resolve_wordpress_credential(
                    self.ctx.session, self.ctx.org.id, data.website_id
                )
                is None
            ):
                raise AppError(
                    "validation",
                    f"errors.marketing_{data.source.value}_not_connected",
                    status_code=422,
                    fields={"website_id": "errors.required"},
                )
        # The caller's own connection is what will sync this link (per-user OAuth); listing the
        # picker options already proved it exists and carries the scope.
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        existing = (
            await self.ctx.session.execute(
                select(MarketingLink).where(
                    MarketingLink.org_id == self.ctx.org.id,
                    MarketingLink.company_id == data.company_id,
                    MarketingLink.source == data.source.value,
                    MarketingLink.external_id == data.external_id,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            reactivated = not existing.active
            existing.active = True
            existing.display_name = data.display_name
            existing.config = data.config
            existing.website_id = data.website_id
            if connection is not None:
                existing.connection_id = connection.id
            await self.ctx.session.flush()
            link = existing
        else:
            link = MarketingLink(
                org_id=self.ctx.org.id,
                company_id=data.company_id,
                website_id=data.website_id,
                source=data.source.value,
                external_id=data.external_id,
                display_name=data.display_name,
                config=data.config,
                connection_id=connection.id if connection else None,
                created_by_user_id=self.ctx.user.id,
            )
            self.ctx.session.add(link)
            await self.ctx.session.flush()
            reactivated = False

        await self._attach_ads_account(link)

        await ActivityService(self.ctx).record(
            "company",
            data.company_id,
            "marketing.linked",
            {
                "source": link.source,
                "name": link.display_name,
                **(
                    {"website": website_names[link.website_id]}
                    if link.website_id in website_names
                    else {}
                ),
            },
        )
        # Kick off the 13-month backfill so sparklines/YoY work from day one — a one-off worker
        # job, deferred so the create's transaction has committed. A queue miss is not fatal.
        if not link.backfill_done and not reactivated:
            try:
                await enqueue(
                    "marketing_backfill_link",
                    str(self.ctx.org.id),
                    str(link.id),
                    _defer_by=5,
                    _job_id=f"marketing-backfill-{link.id}",
                )
            except Exception:
                logger.warning("could not enqueue marketing backfill for link %s", link.id)
        # A link is always created against the *caller's* own connection, so its owner is known
        # here without a second lookup.
        connections = {connection.id: connection} if connection else {}
        owners = (
            {connection.id: self._me_as_owner(connection.email)} if connection else {}
        )
        return self._link_read(link, connections, website_names, owners)

    async def _attach_ads_account(self, link: MarketingLink) -> None:
        """Point a ``gads`` link at the ``google_ads`` module's account row, creating it if needed.

        This is what keeps one truth for "which Ads customer is this client's" while marketing
        keeps its own row. Three properties make it safe to call from a shipped write path:

        * it is an **upsert** on ``(org_id, customer_id)``, so two clients sharing one Ads
          account — a holding and its trading name, an ordinary arrangement — can never raise a
          unique violation and turn this endpoint into a 500;
        * it goes through the **core seam**, so this module never names ``google_ads`` and an
          instance without it simply gets ``AdsNotConfigured``;
        * and it is **advisory**. Failing to record the account must not fail the link the user
          asked for: marketing's own ``external_id`` still answers every call, which is exactly
          the state every pre-existing install is already in.
        """
        if link.source != MarketingSource.GADS.value:
            return
        try:
            ref = await attach_ads_account(
                self.ctx,
                customer_id=link.external_id,
                company_id=link.company_id,
                login_customer_id=str(link.config.get("manager_id") or "") or None,
                connection_id=link.connection_id,
                descriptive_name=link.display_name,
                currency_code=str(link.config.get("currency") or "") or None,
            )
        except AdsNotConfigured:
            return
        except AppError:
            # A refusal the other module decided on (an unreadable customer id, a company
            # outside this caller's horizon). It has already been enforced on *this* row by the
            # checks above, so re-raising here would fail a link for a reason the screen cannot
            # explain. Logged, because a repeated one is a bug.
            logger.warning("could not attach a google ads account for link %s", link.id)
            return
        link.google_ads_account_id = ref.id

    def _me_as_owner(self, google_email: str) -> ConnectionOwner:
        return ConnectionOwner(
            user_id=self.ctx.user.id,
            name=(self.ctx.user.full_name or "").strip() or self.ctx.user.email,
            email=google_email,
            is_me=True,
        )

    async def deactivate_link(self, link_id: uuid.UUID) -> None:
        self.ctx.require("marketing.link.manage")
        link = await self.ctx.repo(MarketingLink).get_or_404(link_id)
        if link.active:
            link.active = False
            await self.ctx.session.flush()
            await ActivityService(self.ctx).record(
                "company",
                link.company_id,
                "marketing.unlinked",
                {"source": link.source, "name": link.display_name},
            )

    # --- per-client settings (#134) ------------------------------------------------------- #
    async def company_settings(self, company_id: uuid.UUID) -> CompanySettingsRead:
        """This client's stored preferences and what they resolve to (#373).

        Gated on ``marketing.metrics.read`` by its route: it is a read, and the reporting
        profile screen needs it to show which keyword source a client's report will use.
        """
        self.ctx.require("marketing.metrics.read")
        await self._company_or_404(company_id)
        row = await self.ctx.session.scalar(
            select(MarketingCompanySettings).where(
                MarketingCompanySettings.org_id == self.ctx.org.id,
                MarketingCompanySettings.company_id == company_id,
            )
        )
        return await self._company_settings_read(company_id, row)

    async def set_company_settings(
        self,
        company_id: uuid.UUID,
        *,
        show_key_events: bool | None = None,
        layout: dict | None = None,
        compare: ComparePeriod | None = None,
        compare_set: bool = False,
        rankings: dict | None = None,
        rankings_set: bool = False,
        report: dict | None = None,
        report_set: bool = False,
    ) -> CompanySettingsRead:
        """Per-client marketing preferences (upsert, one row per org+company).

        ``compare_set`` is what lets ``compare=None`` mean *clear back to the org default*
        rather than *leave alone* (#312, the §18 rule) — the router passes the payload's
        ``model_fields_set``, so "volg standaard" is a choice the dashboard can actually post.

        Gated on ``marketing.link.manage`` — it's configuration, like linking. Two writers,
        kept coherent during the expand release (#192):

        * ``layout`` replaces the stored layout wholesale after validation (``{"sources": {}}``
          clears it back to the defaults); the legacy boolean is rewritten to match the GA4
          tiles so pre-layout readers keep agreeing with what is actually visible.
        * ``show_key_events`` (the #134 toggle) keeps working: where a layout with GA4 tiles
          exists, it edits those tiles (the boolean alone would silently lose the fight).

        Changes land on the client's activity trail (§16), only when something actually flips.
        """
        self.ctx.require("marketing.link.manage")
        await self._company_or_404(company_id)
        row = await self.ctx.session.scalar(
            select(MarketingCompanySettings).where(
                MarketingCompanySettings.org_id == self.ctx.org.id,
                MarketingCompanySettings.company_id == company_id,
            )
        )
        if row is None:
            # Explicit True: the column default only applies at INSERT, and the coherence
            # math below must see the semantic default, not a pre-flush None.
            row = MarketingCompanySettings(
                org_id=self.ctx.org.id, company_id=company_id, show_key_events=True
            )
            self.ctx.session.add(row)
        previous_visible = "keyEvents" in resolved_tiles(
            "ga4", source_layout(row.layout, "ga4"), bool(row.show_key_events)
        )
        previous_layout = row.layout
        previous_compare = row.compare
        previous_rankings = row.rankings
        previous_report = row.report

        if compare_set:
            row.compare = compare.value if compare is not None else None
        if rankings_set:
            # Same absent/`null` rule as `compare` (#373): an explicit null is how a screen says
            # "volg de standaard", which is a choice, not the absence of one.
            row.rankings = rankings or None
        if report_set:
            # …and once more for the per-website rule (#381).
            row.report = report or None

        if layout is not None:
            parsed = CompanyLayout.model_validate(layout)
            validate_layout(parsed)
            stored = parsed.model_dump(exclude_none=True) if parsed.sources else None
            row.layout = stored

        if show_key_events is not None:
            row.show_key_events = show_key_events
            src = source_layout(row.layout, "ga4")
            if src is not None and src.tiles is not None:
                # The toggle edits the curated tiles (#192): add the key-event tiles back in
                # their default place, or take them (and the by-event drill-down) out.
                tiles = [t for t in src.tiles if t not in GA4_KEY_EVENT_TILES]
                if show_key_events:
                    tiles = [
                        m
                        for m in METRICS_BY_SOURCE["ga4"]
                        if m in tiles or m in GA4_KEY_EVENT_TILES
                    ] + [t for t in tiles if t not in METRICS_BY_SOURCE["ga4"]]
                src.tiles = tiles
                if not show_key_events and src.drilldowns is not None:
                    src.drilldowns = [
                        d for d in src.drilldowns if d != GA4_KEY_EVENT_DRILLDOWN
                    ]
                new_layout = dict(row.layout or {"sources": {}})
                new_sources = dict(new_layout.get("sources") or {})
                new_sources["ga4"] = src.model_dump(exclude_none=True)
                new_layout["sources"] = new_sources
                row.layout = new_layout
        # Keep the legacy boolean coherent with the layout for pre-#192 readers.
        row.show_key_events = "keyEvents" in resolved_tiles(
            "ga4", source_layout(row.layout, "ga4"), bool(row.show_key_events)
        )
        await self.ctx.session.flush()

        now_visible = row.show_key_events
        activity = ActivityService(self.ctx)
        if previous_visible != now_visible:
            await activity.record(
                "company",
                company_id,
                "marketing.key_events_enabled"
                if now_visible
                else "marketing.key_events_disabled",
                {},
            )
        if layout is not None and previous_layout != row.layout:
            await activity.record("company", company_id, "marketing.layout_changed", {})
        if compare_set and previous_compare != row.compare:
            # Worth a trail line of its own: it silently re-bases every percentage on the
            # client's dashboard *and* on the report a colleague reads next to it, and "these
            # numbers changed and nobody touched the data" is the question it answers (§16).
            await activity.record(
                "company",
                company_id,
                "marketing.compare_changed",
                {"changes": {"compare": {"from": previous_compare, "to": row.compare}}},
            )
        if rankings_set and previous_rankings != row.rankings:
            await activity.record("company", company_id, "marketing.rankings_changed", {})
        if report_set and previous_report != row.report:
            await activity.record("company", company_id, "marketing.report_changed", {})
        return await self._company_settings_read(company_id, row)

    async def _company_settings_read(
        self, company_id: uuid.UUID, row: MarketingCompanySettings | None
    ) -> CompanySettingsRead:
        org_row = (
            await self.ctx.session.execute(
                select(MarketingSettings.rankings, MarketingSettings.report).where(
                    MarketingSettings.org_id == self.ctx.org.id
                )
            )
        ).first()
        org_rankings, org_report = org_row if org_row else (None, None)
        # Through the repo, so the company horizon applies (§15, #285) — a restricted member
        # must not learn which sources a client they cannot see is linked to.
        stmt = (
            self.ctx.repo(MarketingLink)
            .scoped_select()
            .where(
                MarketingLink.company_id == company_id,
                MarketingLink.active.is_(True),
            )
        )
        links = list((await self.ctx.session.execute(stmt)).scalars())
        linked = [link.source for link in links]
        resolved = resolve_rankings(org_rankings, row.rankings if row else None)
        report_resolved = resolve_report(org_report, row.report if row else None)
        return CompanySettingsRead(
            company_id=company_id,
            show_key_events=bool(row.show_key_events) if row else True,
            layout=row.layout if row else None,
            compare=ComparePeriod(row.compare) if row and row.compare else None,
            compare_resolved=resolve_compare(
                row.compare if row else None, await self._org_default_compare()
            ),
            rankings=row.rankings if row else None,
            rankings_resolved=RankingSettingsRead(**resolved.as_dict()),
            linked_sources=[MarketingSource(source) for source in sorted(set(linked))],
            # The same one function the gatherer asks, so a screen promising "positions from
            # Search Console" and a run that produces none is a contradiction that cannot arise.
            keyword_source=effective_source(
                resolved,
                has_seranking=MarketingSource.SERANKING.value in linked,
                has_search_console=MarketingSource.GSC.value in linked,
            ),
            report=row.report if row else None,
            report_resolved=ReportSplitSettingsRead(**report_resolved.as_dict()),
            links=[LinkBrief.model_validate(link) for link in links],
        )

    # --- pickers (#132) ------------------------------------------------------------------- #
    async def _others_with_scope(self, scope: str) -> list[ConnectionOwner]:
        """Colleagues whose active connection already carries ``scope``.

        Only ever asked on the *empty* branches of the picker, so it costs a query exactly where
        there is nothing else to show. Names, not counts: "already connected via Stan" tells you
        whom to ask; "1 other connection" tells you nothing you can act on.
        """
        connections, owners = await self._connections_by_id()
        return [
            owners[connection.id]
            for connection in connections.values()
            if connection.user_id != self.ctx.user.id
            and connection.status == ConnectionStatus.ACTIVE.value
            and scope in set(connection.scopes or [])
            and connection.id in owners
        ]

    async def available_accounts(
        self,
        source: MarketingSource,
        website_id: uuid.UUID | None = None,
        *,
        refresh: bool = False,
    ) -> AccountsResponse:
        self.ctx.require("marketing.link.manage")
        adapter = source_for(source.value)
        if source_auth(source.value) != AUTH_GOOGLE:
            return await self._keyed_accounts(
                source, adapter, website_id=website_id, refresh=refresh
            )
        flag = _CONNECT_FLAG[source.value]
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            return AccountsResponse(
                source=source,
                connected=False,
                connect_flag=flag,
                connected_via=await self._others_with_scope(adapter.scope),
            )
        has_scope = adapter.scope in set(connection.scopes or [])
        if not has_scope or connection.status != ConnectionStatus.ACTIVE.value:
            return AccountsResponse(
                source=source,
                connected=True,
                has_scope=has_scope,
                connect_flag=flag,
                connected_via=await self._others_with_scope(adapter.scope),
            )

        # Google Ads needs a per-org developer token; with none the picker teaches "not configured"
        # instead of calling Google (#134). GA4/GSC pass through with token=None.
        ads_token: str | None = None
        if source == MarketingSource.GADS:
            ads_token = await self._resolve_ads_developer_token()
            if not ads_token:
                return AccountsResponse(
                    source=source, connected=True, has_scope=True, configured=False,
                    connect_flag=flag,
                )

        cache_key = f"schakl:marketing:accounts:{connection.id}:{source.value}"
        redis = get_redis()
        cached = None if refresh else await redis.get(cache_key)
        if cached is not None:
            options = [AvailableAccount(**item) for item in json.loads(cached)]
        else:
            try:
                with developer_token_scope(ads_token):
                    # Pool connection released during the live account listing
                    # (docs/PERFORMANCE.md).
                    async with (
                        google_client.acting_as(self.ctx.session, self.ctx.org, connection)
                        as gclient,
                        self.ctx.release_db(),
                    ):
                        fetched = await adapter.list_accounts(gclient)
            except AdsNotConfigured:
                return AccountsResponse(
                    source=source, connected=True, has_scope=True, configured=False,
                    connect_flag=flag,
                )
            except Exception as exc:  # noqa: BLE001 — a live fetch failure is a reconnect prompt
                if await google_client.is_oauth_error(exc):
                    await google_client.mark_connection_error(
                        self.ctx.session, self.ctx.org, connection, str(exc)
                    )
                    return AccountsResponse(
                        source=source, connected=True, has_scope=False, connect_flag=flag,
                        error="errors.google_connection_error",
                    )
                detail = google_client.describe_api_error(exc)
                logger.warning(
                    "marketing accounts fetch failed for %s (%s): %s",
                    source.value,
                    await google_client.oauth_client_hint(self.ctx.session, self.ctx.org.id),
                    detail or exc,
                )
                # Google saying the token lacks this scope *is* ``has_scope=False`` — the picker
                # already teaches that case by name, with the reconnect that actually cures it.
                return AccountsResponse(
                    source=source, connected=True,
                    has_scope=detail is None or not detail.scope_insufficient,
                    connect_flag=flag,
                    error=_failure_key(detail, "marketing.accounts_error", source=source.value),
                )
            options = [
                AvailableAccount(
                    external_id=opt.external_id,
                    display_name=opt.display_name,
                    account_hint=opt.account_hint,
                    config=opt.config,
                )
                for opt in fetched
            ]
            await redis.set(
                cache_key,
                json.dumps([opt.model_dump() for opt in options]),
                ex=600,  # 10 min; the picker refreshes in the background on open (#132)
            )
        return AccountsResponse(
            source=source, connected=True, has_scope=True, accounts=options, connect_flag=flag
        )

    # --- metrics for the panel + tab (#133), stored data only ----------------------------- #
    async def company_marketing(
        self,
        company_id: uuid.UUID,
        range_days: int,
        period: str | None = None,
        *,
        with_connections: bool = False,
    ) -> CompanyMarketing:
        """This client's linked sources, folded.

        ``with_connections`` is opt-in rather than always-on (#411): the connections row is a
        statement a *panel* wants and the tab, ``/marketing`` and the client's portal widget do
        not, and a payload that grows a cost for one of its four callers is how a hub gets slow
        one field at a time (docs/PERFORMANCE.md).
        """
        self.ctx.require("marketing.metrics.read")
        await self._company_or_404(company_id)
        today = await self._today()

        # The tenant's own name for a source rides the same statement (#446): it is what the
        # marketing page, the client hub and the client's homepage all print, so an agency
        # selling "Breik. Analytics" reads the same word on every screen that shows it. Only the
        # vendor-free *default* stays a portal-only substitution (`source_label`).
        prefs, org_default, portal_labels = await self._prefs_with_default(company_id)
        # The client's own choice wins, then the agency's, then ours (#312). Resolved here and
        # nowhere else on this path, so the window the numbers came from and the window the
        # screen names are the same object rather than two computations that agree today.
        window = compare_windows(
            today, period_token(period, range_days), resolve_compare(prefs.compare, org_default)
        )
        cur_start, cur_end = window.current_start, window.current_end
        # Derived from the resolved span, never echoed back from the request: a caller who asked
        # for "2026-07" gets 31, and a screen that draws a chart off this cannot disagree with
        # the dates beside it.
        range_days = period_days(cur_start, cur_end)
        prev_start, prev_end = window.start, window.end

        show_key_events, layout = prefs.show_key_events, prefs.layout
        links = await self._links(company_id=company_id)
        connections, owners = await self._connections_by_id()
        # The client's websites: group labels for linked sources + options for the pickers.
        websites = await self._company_websites(company_id)
        website_names = {w.id: w.name for w in websites}
        can_manage = self.ctx.can("marketing.link.manage")
        sources: list[SourceMetrics] = []
        if links:
            metrics_by_link = await self._metrics_for_links(
                [link.id for link in links], window.spans()
            )
            for link in links:
                hidden = source_hidden(layout, link.source)
                # A hidden source is dropped from the payload entirely, so the portal/client
                # never receives it (#192). A manager keeps it, flagged, so edit mode can list
                # every linked source and offer to re-enable it.
                if hidden and not can_manage:
                    continue
                sm = self._source_metrics(
                    link,
                    metrics_by_link.get(link.id, {}),
                    connections,
                    cur_start,
                    cur_end,
                    prev_start,
                    prev_end,
                    show_key_events=show_key_events,
                    layout=layout,
                    website_names=website_names,
                    owners=owners,
                    portal_labels=portal_labels,
                )
                sm.hidden = hidden
                sources.append(sm)
        # Borrowed through the seam, never read out of `reports`: the lender's own service
        # decides what a client-facing login may see (#300).
        narrative = await latest_narrative(self.ctx, company_id)
        # The same rule one seam over (#411). A client-facing login never receives them: what is
        # measuring their website is the agency's working surface, and the provider's own
        # permission (`google_tag_manager.container.read`) is not one a portal membership holds
        # anyway — checked here as well, because "the permission happens to exclude them" is a
        # coincidence and `is_portal` is the statement (§15, #274).
        connections: list[MarketingConnection] = []
        if with_connections and not self.ctx.is_portal:
            connections = [
                MarketingConnection(
                    kind="gtm",
                    label=portal_labels.get("gtm") or None,
                    id=row.id,
                    external_id=row.public_id,
                    name=row.name,
                    status=row.status,
                    last_error=row.last_error,
                    pending_changes=row.workspace_changes,
                    live_count=row.tag_count,
                    observed_at=row.observed_at,
                    deep_link=row.deep_link,
                    href=f"/marketing/tag-manager/{row.id}",
                )
                for row in await company_containers(self.ctx, company_id)
            ]
        return CompanyMarketing(
            company_id=company_id,
            range_days=range_days,
            compare=window,
            # The stored value, not the resolved one: the editor's select must be able to show
            # "volg standaard" as the state it actually is (#312), and only a manager configures.
            compare_setting=(
                ComparePeriod(prefs.compare)
                if can_manage and prefs.compare in tuple(ComparePeriod)
                else None
            ),
            compare_default=org_default,
            sources=sources,
            connections=connections,
            needs_connection=not await self._any_connection(),
            can_manage=can_manage,
            show_key_events=show_key_events,
            # The stored layout feeds the tab's edit mode (#192) — manager-only, like the
            # settings write it configures.
            layout=layout if can_manage else None,
            websites=websites,
            narrative=narrative.as_payload() if narrative is not None else None,
        )

    async def _metrics_for_links(
        self, link_ids: list[uuid.UUID], spans: list[tuple[date, date]]
    ) -> dict[uuid.UUID, dict[date, dict[str, Any]]]:
        """One query for every link's daily rows in ``spans`` → {link_id: {day: metrics}}.

        **The spans, not their hull.** This used to take one contiguous ``[prev_start, cur_end]``,
        which was the same thing while the comparison was always the span immediately before.
        Under a year-over-year comparison (#312) the two windows are a year apart, and reading
        the hull would drag eleven months of rows nobody looks at through the session on every
        dashboard render — on the 12-month range, three years of them. An ``OR`` of two bounded
        ranges keeps the index scan on ``(org_id, link_id, date)`` and the row count at what the
        screen actually draws (docs/PERFORMANCE.md).
        """
        if not link_ids or not spans:
            return {}
        rows = (

                await self.ctx.session.execute(
                    select(
                        MarketingMetricDaily.link_id,
                        MarketingMetricDaily.date,
                        MarketingMetricDaily.metrics,
                        MarketingMetricDaily.currency,
                    ).where(
                        MarketingMetricDaily.org_id == self.ctx.org.id,
                        MarketingMetricDaily.link_id.in_(link_ids),
                        or_(
                            *(
                                and_(
                                    MarketingMetricDaily.date >= start,
                                    MarketingMetricDaily.date <= end,
                                )
                                for start, end in spans
                            )
                        ),
                    )
                )

        ).all()
        out: dict[uuid.UUID, dict[date, dict[str, Any]]] = defaultdict(dict)
        for link_id, day, metrics, currency in rows:
            payload = dict(metrics or {})
            payload["_currency"] = currency
            out[link_id][day] = payload
        return out

    async def _own_source_labels(self) -> dict[str, str]:
        """The tenant's own names and nothing else — for the screens that print a source name
        beside no metrics row (the client picker's tiles, the cross-client grid), which resolve
        the catalog default in the browser."""
        labels = await self._portal_source_labels()
        return {k: v for k, v in labels.items() if k != _LOCALE_KEY}

    async def _portal_source_labels(self) -> dict[str, str]:
        """The tenant's source names (#446), ``{source: label}`` — org-level, one read. Absent
        keys mean the code default (:func:`source_label`), translated in the org's own display
        language where a default is substituted: a client reads the tenant's product, in the
        tenant's language, and never a colleague's."""
        row = await self.ctx.session.execute(
            select(MarketingSettings.portal_source_labels, OrgSettings.default_locale)
            .select_from(OrgSettings)
            .outerjoin(MarketingSettings, MarketingSettings.org_id == OrgSettings.org_id)
            .where(OrgSettings.org_id == self.ctx.org.id)
        )
        stored, locale = row.first() or (None, None)
        return _shape_source_labels(stored, locale)

    def _source_metrics(
        self,
        link: MarketingLink,
        daily: dict[date, dict[str, Any]],
        connections: dict[uuid.UUID, GoogleConnection],
        cur_start: date,
        cur_end: date,
        prev_start: date,
        prev_end: date,
        *,
        show_key_events: bool = True,
        layout: dict | None = None,
        website_names: dict[uuid.UUID, str] | None = None,
        owners: dict[uuid.UUID, ConnectionOwner] | None = None,
        portal_labels: dict[str, str] | None = None,
    ) -> SourceMetrics:
        adapter = source_for(link.source)
        # A client-facing login gets the numbers and nothing about the machinery behind them
        # (#446/#447/#448): not whose Google grant they ride, not a link into the supplier's
        # console, and not the supplier's name where the tenant has chosen another. Decided here
        # — the one place a source row is built — so the widget, the tab and an MCP client
        # cannot disagree, and `is_portal` is the statement rather than a permission that
        # happens to exclude them (§15, #274).
        portal = self.ctx.is_portal
        current_rows = [m for day, m in daily.items() if cur_start <= day <= cur_end]
        prev_rows = [m for day, m in daily.items() if prev_start <= day <= prev_end]
        cur_agg = aggregate(link.source, current_rows)
        prev_agg = aggregate(link.source, prev_rows)
        # The client's curated layout (#192) — or, where none exists, the legacy key-events
        # gate (#134) — decides which tiles exist. Hidden tiles are dropped from the payload
        # entirely (never a client-side hide), so no consumer ever sees them.
        src_layout = source_layout(layout, link.source)
        metrics = resolved_tiles(link.source, src_layout, show_key_events)
        kpis = {
            metric: KpiValue(
                current=cur_agg.get(metric, 0.0),
                previous=prev_agg.get(metric, 0.0),
                delta_pct=_delta_pct(cur_agg.get(metric, 0.0), prev_agg.get(metric, 0.0)),
                lower_is_better=metric in LOWER_IS_BETTER,
            )
            for metric in metrics
        }

        # A gap-free daily series across the current window (0-fill), for sparkline/trend.
        span = (cur_end - cur_start).days + 1
        dates = [cur_start + timedelta(days=i) for i in range(span)]
        series_metrics: dict[str, list[float]] = {
            metric: [float(daily.get(day, {}).get(metric, 0) or 0) for day in dates]
            for metric in metrics
        }

        channels = None
        if link.source == MarketingSource.GA4.value:
            channels = defaultdict(float)
            for day in dates:
                for group, value in (daily.get(day, {}).get("channels", {}) or {}).items():
                    channels[group] += float(value or 0)
            channels = dict(channels)

        currency = next(
            (m.get("_currency") for m in current_rows if m.get("_currency")),
            (link.config or {}).get("currency"),
        )
        return SourceMetrics(
            link_id=link.id,
            source=MarketingSource(link.source),
            display_name=link.display_name,
            external_id=link.external_id,
            website_id=link.website_id,
            website_name=(website_names or {}).get(link.website_id) if link.website_id else None,
            health=self._health(link, connections, bool(daily)),
            last_error=link.last_error,
            last_synced_at=link.last_synced_at,
            connection_owner=(
                (owners or {}).get(link.connection_id)
                if link.connection_id and not portal
                else None
            ),
            currency=currency,
            deep_link="" if portal else adapter.deep_link(link.external_id, link.config or {}),
            label=source_label(link.source, portal_labels or {}, portal=portal),
            primary_metric=resolved_primary(link.source, src_layout, metrics),
            kpis=kpis,
            series=SeriesData(dates=dates, metrics=series_metrics),
            channels=channels,
            tiles=metrics,
            tile_labels=(src_layout.labels if src_layout else {}),
            drilldowns=resolved_drilldowns(
                link.source, adapter.drilldowns, src_layout, metrics
            ),
            ai_visibility=(
                SourceAiVisibility(**adapter.ai_visibility(link.external_id, link.config or {}))
                if not portal and hasattr(adapter, "ai_visibility")
                else None
            ),
        )

    def _health(
        self,
        link: MarketingLink,
        connections: dict[uuid.UUID, GoogleConnection],
        has_data: bool,
    ) -> str:
        # "Disconnected" is a statement about a **Google grant**, so only a Google-auth source
        # can be in it (#399). SE Ranking rides one agency API key and Rank Math a per-website
        # WordPress password; neither ever holds a `connection_id`, so asking this of them
        # painted a red *"De Google-verbinding van deze koppeling is weg"* over a link that was
        # working — the same mistake the tab used to make one level up, where one missing Google
        # credential blanked the whole screen. What is wrong with a keyed source shows up as its
        # own `last_error`, which the next branch already reads.
        if source_auth(link.source) == AUTH_GOOGLE:
            connection = connections.get(link.connection_id) if link.connection_id else None
            if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
                return "disconnected"
        if link.last_error:
            return "error"
        if not link.backfill_done and not has_data:
            return "pending"
        return "ok" if has_data else "pending"

    # --- drill-downs (#133), live behind a Redis TTL -------------------------------------- #
    async def _keyed_drilldown(
        self,
        link: MarketingLink,
        adapter: Any,
        kind: str,
        start: date,
        end: date,
        deep_link: str,
        source: MarketingSource,
        src_layout: Any,
    ) -> DrilldownResponse:
        """The drill-down path for a source with no per-user connection (#300).

        Cached and released exactly like the Google one; what differs is the unavailable
        reason. "Reconnect your Google account" is meaningless advice for a missing agency
        API key, and an admin who follows it ends up on a screen that cannot help them — which
        is why the reason comes from :class:`SourceNotConfigured`, whose wording is per auth
        kind: an org key is set in Instellingen, a site credential on one client's website.
        """
        redis = get_redis()
        cache_key = f"schakl:marketing:drill:{link.id}:{kind}:{start}:{end}"
        cached = await redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            return DrilldownResponse(
                source=source, kind=kind, columns=payload["columns"],
                rows=[DrilldownRowOut(**row) for row in payload["rows"]],
                deep_link=deep_link,
            )
        try:
            async with (
                keyed_client(
                    self.ctx.session, self.ctx.org.id, link.source, link.website_id
                ) as client,
                self.ctx.release_db(),
            ):
                table = await adapter.drilldown(
                    client, link.external_id, kind, start, end, link.config or {}
                )
        except SourceNotConfigured as exc:
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason=exc.message_key, deep_link=deep_link,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("marketing %s drilldown failed: %s", link.source, exc)
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason=_org_key_error(exc, link.source), deep_link=deep_link,
            )
        rows = [
            DrilldownRowOut(label=row.label, href=row.href, metrics=row.metrics)
            for row in table.rows
        ]
        await redis.set(
            cache_key,
            json.dumps(
                {
                    "columns": table.columns,
                    "rows": [row.model_dump(mode="json") for row in rows],
                }
            ),
            ex=_DRILLDOWN_TTL,
        )
        return DrilldownResponse(
            source=source, kind=kind, columns=table.columns, rows=rows, deep_link=deep_link
        )

    async def drilldown(
        self,
        company_id: uuid.UUID,
        link_id: uuid.UUID,
        kind: str,
        range_days: int,
        period: str | None = None,
    ) -> DrilldownResponse:
        response = await self._drilldown(company_id, link_id, kind, range_days, period)
        if self.ctx.is_portal:
            # The same redaction `_source_metrics` applies one level up (#446/#447): a client
            # gets the table and nothing about the machinery. No link into the supplier's
            # console, and — where the table could not be read — no sentence naming the
            # supplier or telling them to "reconnect" or "ask your administrator": every
            # reason is the agency's to act on, so the client reads one neutral line.
            response.deep_link = ""
            if not response.available:
                response.unavailable_reason = "marketing.portal_unavailable"
        return response

    async def _drilldown(
        self,
        company_id: uuid.UUID,
        link_id: uuid.UUID,
        kind: str,
        range_days: int,
        period: str | None = None,
    ) -> DrilldownResponse:
        self.ctx.require("marketing.metrics.read")
        link = await self.ctx.repo(MarketingLink).get_or_404(link_id)
        if link.company_id != company_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        adapter = source_for(link.source)
        if kind not in adapter.drilldowns:
            raise AppError("validation", "errors.validation", status_code=422)
        # The client's layout decides which drill-downs exist (#192) — including the legacy
        # key-events gate (#134): a hidden keyEvents tile takes its breakdown with it.
        prefs = await self._company_settings(company_id)
        show_key_events, layout = prefs.show_key_events, prefs.layout
        src_layout = source_layout(layout, link.source)
        tiles = resolved_tiles(link.source, src_layout, show_key_events)
        if kind not in resolved_drilldowns(link.source, adapter.drilldowns, src_layout, tiles):
            raise AppError("validation", "errors.validation", status_code=422)
        today = await self._today()
        start, end = resolve_period(
            period_token(period, range_days), today, max_days=MAX_RANGE_DAYS
        )
        deep_link = adapter.deep_link(link.external_id, link.config or {})
        source = MarketingSource(link.source)

        if source_auth(link.source) != AUTH_GOOGLE:
            return await self._keyed_drilldown(
                link, adapter, kind, start, end, deep_link, source, src_layout
            )

        connection = (
            await self.ctx.session.get(GoogleConnection, link.connection_id)
            if link.connection_id
            else None
        )
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason="marketing.disconnected", deep_link=deep_link,
            )

        redis = get_redis()
        # Keyed on the resolved **dates**, never on the day count: once a period can be named
        # (#316), "2026-07" and "2026-06" are both 30-ish days, and a key that says only "31"
        # would serve June's table for July — one number different, every row wrong.
        cache_key = f"schakl:marketing:drill:{link.id}:{kind}:{start}:{end}"
        cached = await redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            raw_rows = [DrilldownRowOut(**row) for row in payload["rows"]]
            # Labels ride the *layout*, not the cached Google rows, and are applied here on the
            # way out — so a relabel shows immediately instead of waiting for the TTL (#192).
            return DrilldownResponse(
                source=source, kind=kind, columns=payload["columns"],
                rows=self._label_event_rows(raw_rows, link.source, kind, src_layout),
                deep_link=deep_link,
            )
        ads_token = (
            await self._resolve_ads_developer_token()
            if source == MarketingSource.GADS
            else None
        )
        try:
            with developer_token_scope(ads_token):
                # The live Google fetch runs with the pool connection released — a cold
                # drill-down takes seconds, and six of them fire per tab (docs/PERFORMANCE.md).
                async with (
                    google_client.acting_as(self.ctx.session, self.ctx.org, connection)
                    as gclient,
                    self.ctx.release_db(),
                ):
                    table = await adapter.drilldown(
                        gclient, link.external_id, kind, start, end, link.config or {}
                    )
        except AdsNotConfigured:
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason="marketing.ads_not_configured", deep_link=deep_link,
            )
        except Exception as exc:  # noqa: BLE001
            if await google_client.is_oauth_error(exc):
                await google_client.mark_connection_error(
                    self.ctx.session, self.ctx.org, connection, str(exc)
                )
                reason = "marketing.disconnected"
            else:
                detail = google_client.describe_api_error(exc)
                logger.warning(
                    "marketing drilldown failed (%s/%s, %s): %s",
                    link.source,
                    kind,
                    await google_client.oauth_client_hint(self.ctx.session, self.ctx.org.id),
                    detail or exc,
                )
                reason = _failure_key(detail, "marketing.accounts_error", source=link.source)
            return DrilldownResponse(
                source=source, kind=kind, available=False, unavailable_reason=reason,
                deep_link=deep_link,
            )
        # Cache the *raw* Google rows (label = the event name), label-independent, so a relabel
        # never has to wait for the TTL — the custom labels are applied after retrieval (#192).
        raw_rows = [
            DrilldownRowOut(label=row.label, href=row.href, metrics=row.metrics)
            for row in table.rows
        ]
        await redis.set(
            cache_key,
            json.dumps({"columns": table.columns, "rows": [r.model_dump() for r in raw_rows]}),
            ex=_DRILLDOWN_TTL,
        )
        return DrilldownResponse(
            source=source, kind=kind, columns=table.columns,
            rows=self._label_event_rows(raw_rows, link.source, kind, src_layout),
            deep_link=deep_link,
        )

    def _label_event_rows(
        self,
        rows: list[DrilldownRowOut],
        source: str,
        kind: str,
        src_layout: Any,
    ) -> list[DrilldownRowOut]:
        """Apply the client's per-key-event labels to a GA4 ``key_events`` drill-down (#192).

        Each row's raw ``eventName`` is kept as ``key`` (the stable id the editor keys on) and its
        ``label`` becomes the tenant's custom name when one is set, else the raw event name. A
        no-op for every other source/kind, which carry no per-event labels."""
        if source != MarketingSource.GA4.value or kind != GA4_KEY_EVENT_DRILLDOWN:
            return rows
        locale = "nl" if (self.ctx.user.locale or "nl").startswith("nl") else "en"
        return [
            DrilldownRowOut(
                label=resolve_event_label(src_layout, row.label, locale) or row.label,
                key=row.label,
                href=row.href,
                metrics=row.metrics,
            )
            for row in rows
        ]

    # --- cross-client overview (#133), stored data only ----------------------------------- #
    async def overview(
        self, range_days: int, sort: str | None, period: str | None = None
    ) -> OverviewResponse:
        """The cross-client grid (#133).

        Its deltas use the **org default** comparison, never each client's own override (#312):
        a board whose rows are sorted against denominators that differ per row ranks nothing.
        The per-client setting governs that client's own dashboard, which is the screen it was
        chosen for; here the grid names the one period it used, above the table.
        """
        self.ctx.require("marketing.overview.read")
        today = await self._today()
        window = compare_windows(
            today, period_token(period, range_days), await self._org_default_compare()
        )
        cur_start, cur_end = window.current_start, window.current_end
        prev_start, prev_end = window.start, window.end
        range_days = period_days(cur_start, cur_end)

        # The cross-client board is hand-built (it pairs each link with its client's name and
        # folds the metrics per company), so it never travelled ``scoped_select()`` and carried
        # no horizon at all: a membership scoped to one company group read every client's
        # marketing numbers on the one screen that lists them all (#285, the #240 shape).
        stmt = (
            select(MarketingLink, Company.name)
            .join(Company, Company.id == MarketingLink.company_id)
            .where(MarketingLink.org_id == self.ctx.org.id, MarketingLink.active.is_(True))
        )
        horizon = self.ctx.repo(MarketingLink).horizon_condition()
        if horizon is not None:
            stmt = stmt.where(horizon)
        pairs = (await self.ctx.session.execute(stmt)).all()
        source_labels = await self._own_source_labels()
        if not pairs:
            return OverviewResponse(
                range_days=range_days,
                compare=window,
                rows=[],
                total=0,
                source_labels=source_labels,
            )

        links = [pair[0] for pair in pairs]
        names = {pair[0].company_id: pair[1] for pair in pairs}
        metrics_by_link = await self._metrics_for_links(
            [link.id for link in links], window.spans()
        )

        # company -> source -> (current rows, previous rows)
        by_company: dict[uuid.UUID, dict[str, tuple[list, list]]] = defaultdict(
            lambda: defaultdict(lambda: ([], []))
        )
        sources_present: dict[uuid.UUID, set[str]] = defaultdict(set)
        for link in links:
            sources_present[link.company_id].add(link.source)
            daily = metrics_by_link.get(link.id, {})
            cur, prev = by_company[link.company_id][link.source]
            for day, m in daily.items():
                # Both windows are tested, never "current else previous": the two are no longer
                # adjacent, so an else-branch would file a day from neither window as previous.
                if cur_start <= day <= cur_end:
                    cur.append(m)
                if prev_start <= day <= prev_end:
                    prev.append(m)

        settings = await self._settings_map(list(by_company.keys()))
        rows: list[OverviewRow] = []
        for company_id, per_source in by_company.items():
            prefs = settings.get(company_id, CompanyPrefs())
            show_key_events, layout = prefs.show_key_events, prefs.layout
            agg_cur = {s: aggregate(s, buckets[0]) for s, buckets in per_source.items()}
            agg_prev = {s: aggregate(s, buckets[1]) for s, buckets in per_source.items()}
            # Which metric keys this client's layout leaves visible, per source (#192) — the
            # grid respects per-company hidden metrics exactly like the panel/tab.
            visible = {
                s: set(resolved_tiles(s, source_layout(layout, s), show_key_events))
                for s in per_source
            }
            metrics: dict[str, KpiValue] = {}
            for col, (src, metric) in _OVERVIEW_COLUMNS.items():
                if src not in per_source:
                    continue
                # A hidden tile shows no number here either, matching the panel/tab (#134/#192).
                if metric not in visible.get(src, set()):
                    continue
                cur_v = agg_cur[src].get(metric, 0.0)
                prev_v = agg_prev[src].get(metric, 0.0)
                metrics[col] = KpiValue(
                    current=cur_v,
                    previous=prev_v,
                    delta_pct=_delta_pct(cur_v, prev_v),
                    lower_is_better=metric in LOWER_IS_BETTER,
                )
            present = sorted(sources_present[company_id])
            # The grid's toggle reflects the *effective* visibility: for a layout-curated
            # client the tiles decide, not the legacy boolean (#192).
            key_events_visible = (
                "keyEvents" in visible["ga4"] if "ga4" in visible else show_key_events
            )
            rows.append(
                OverviewRow(
                    company_id=company_id,
                    company_name=names.get(company_id, ""),
                    sources_present=[MarketingSource(s) for s in present],
                    metrics=metrics,
                    show_key_events=key_events_visible,
                )
            )
        rows = self._sort_overview(rows, sort)
        return OverviewResponse(
            range_days=range_days,
            compare=window,
            rows=rows,
            total=len(rows),
            source_labels=source_labels,
        )

    def _sort_overview(self, rows: list[OverviewRow], sort: str | None) -> list[OverviewRow]:
        key = (sort or "company_name").lstrip("-")
        descending = bool(sort and sort.startswith("-"))
        if key == "company_name":
            return sorted(rows, key=lambda r: r.company_name.lower(), reverse=descending)
        if key not in _OVERVIEW_COLUMNS:
            return sorted(rows, key=lambda r: r.company_name.lower())
        # Rows missing the metric sort last regardless of direction (they have no number).
        def metric_key(row: OverviewRow) -> tuple[int, float]:
            kpi = row.metrics.get(key)
            return (0, kpi.current) if kpi is not None else (1, 0.0)

        present = [r for r in rows if key in r.metrics]
        absent = [r for r in rows if key not in r.metrics]
        present.sort(key=lambda r: r.metrics[key].current, reverse=descending)
        return present + sorted(absent, key=lambda r: r.company_name.lower())

    async def summary(
        self, range_days: int, limit: int, period: str | None = None
    ) -> MarketingSummary:
        """The My Day widget's digest (#254): top linked clients by one headline KPI each
        (GA4 sessions where linked and visible, else GSC clicks), from stored data only.

        Rides ``marketing.metrics.read`` like the per-company read it teases, so — unlike
        ``overview``, whose grid is ``marketing.overview.read`` — it must honour the company
        horizon (#191): the portal ``client`` role holds the same read, and this may never
        return a row its caller could not fetch client-by-client. Per-client curation (#192)
        applies exactly like the panel/tab: a hidden tile feeds no number here either.

        The comparison is the org default, like the grid's and for the same reason (#312): this
        is one list of several clients, and the card names the period once above all of them.
        """
        self.ctx.require("marketing.metrics.read")
        today = await self._today()
        window = compare_windows(
            today, period_token(period, range_days), await self._org_default_compare()
        )
        cur_start, cur_end = window.current_start, window.current_end
        prev_start, prev_end = window.start, window.end
        range_days = period_days(cur_start, cur_end)

        stmt = (
            select(MarketingLink, Company.name)
            .join(Company, Company.id == MarketingLink.company_id)
            .where(MarketingLink.org_id == self.ctx.org.id, MarketingLink.active.is_(True))
        )
        if self.ctx.company_scope is not None:
            stmt = stmt.where(MarketingLink.company_id.in_(self.ctx.company_scope))
        pairs = (await self.ctx.session.execute(stmt)).all()
        if not pairs:
            return MarketingSummary(
                range_days=range_days, compare=window, linked_total=0, rows=[]
            )

        names = {pair[0].company_id: pair[1] for pair in pairs}
        headline_links = [
            pair[0]
            for pair in pairs
            if pair[0].source in (MarketingSource.GA4.value, MarketingSource.GSC.value)
        ]
        metrics_by_link = await self._metrics_for_links(
            [link.id for link in headline_links], window.spans()
        )

        # company -> source -> (current rows, previous rows), the overview's bucketing — both
        # windows tested, because they need not be adjacent (#312).
        by_company: dict[uuid.UUID, dict[str, tuple[list, list]]] = defaultdict(
            lambda: defaultdict(lambda: ([], []))
        )
        for link in headline_links:
            daily = metrics_by_link.get(link.id, {})
            cur, prev = by_company[link.company_id][link.source]
            for day, m in daily.items():
                if cur_start <= day <= cur_end:
                    cur.append(m)
                if prev_start <= day <= prev_end:
                    prev.append(m)

        settings_map = await self._settings_map(list(by_company.keys()))
        rows: list[MarketingSummaryRow] = []
        for company_id, per_source in by_company.items():
            prefs = settings_map.get(company_id, CompanyPrefs())
            show_key_events, layout = prefs.show_key_events, prefs.layout
            for source, metric in (
                (MarketingSource.GA4.value, "sessions"),
                (MarketingSource.GSC.value, "clicks"),
            ):
                if source not in per_source:
                    continue
                visible = resolved_tiles(source, source_layout(layout, source), show_key_events)
                if metric not in visible:
                    continue
                cur_v = aggregate(source, per_source[source][0]).get(metric, 0.0)
                prev_v = aggregate(source, per_source[source][1]).get(metric, 0.0)
                rows.append(
                    MarketingSummaryRow(
                        company_id=company_id,
                        company_name=names.get(company_id, ""),
                        metric=metric,
                        kpi=KpiValue(
                            current=cur_v,
                            previous=prev_v,
                            delta_pct=_delta_pct(cur_v, prev_v),
                            lower_is_better=metric in LOWER_IS_BETTER,
                        ),
                    )
                )
                break
        rows.sort(key=lambda r: (-r.kpi.current, r.company_name.lower()))
        # A client whose links feed neither headline (Ads-only, or both tiles curated away)
        # still counts: the "top n of this" note must name what the list leaves out.
        return MarketingSummary(
            range_days=range_days,
            compare=window,
            linked_total=len(names),
            rows=rows[:limit],
        )

    # --- the client picker on Marketing ----------------------------------------------------- #
    async def linked_clients(self, limit: int = 200) -> MarketingClientList:
        """Every client with at least one linked source — what the Marketing picker offers.

        The screen it serves used to be a dropdown over **all** companies, which asked the
        marketeer to remember which of two hundred client names has a dashboard behind it. The
        list could not tell them, so most of it led to an empty screen. Tiles can tell them, and
        this is the read that lets them: one query, no metric fold.

        It is deliberately not :meth:`overview`. That one folds every stored daily row to rank
        clients and rides ``marketing.overview.read``, a manager permission (docs/UX.md) — a
        picker built on it would be a screen a marketeer holding exactly the read it leads to
        could not open. So this rides ``marketing.metrics.read``, like the dashboard it picks
        for, and is horizon-scoped for the same reason :meth:`summary` is: the portal ``client``
        role holds that read too, and a list may never name a client its caller cannot fetch.
        """
        self.ctx.require("marketing.metrics.read")
        stmt = (
            select(MarketingLink, Company.name)
            .join(Company, Company.id == MarketingLink.company_id)
            .where(MarketingLink.org_id == self.ctx.org.id, MarketingLink.active.is_(True))
        )
        horizon = self.ctx.repo(MarketingLink).horizon_condition()
        if horizon is not None:
            stmt = stmt.where(horizon)
        pairs = (await self.ctx.session.execute(stmt)).all()

        names: dict[uuid.UUID, str] = {}
        # company -> source -> [link count, worst state]
        per_company: dict[uuid.UUID, dict[str, list]] = defaultdict(dict)
        for link, company_name in pairs:
            names[link.company_id] = company_name
            state = "error" if link.last_error else ("ok" if link.last_synced_at else "pending")
            entry = per_company[link.company_id].setdefault(link.source, [0, "ok"])
            entry[0] += 1
            # The worst of a client's links of one source wins: a chip reading "ok" while one of
            # the two properties behind it had been failing for a week would be the picker
            # telling somebody there is nothing here to look into.
            if _CLIENT_STATE_ORDER[state] > _CLIENT_STATE_ORDER[entry[1]]:
                entry[1] = state

        order = {source.value: index for index, source in enumerate(MarketingSource)}
        rows = [
            MarketingClientRow(
                company_id=company_id,
                company_name=names.get(company_id, ""),
                sources=[
                    MarketingClientSource(source=MarketingSource(source), links=count, state=state)
                    for source, (count, state) in sorted(
                        sources.items(), key=lambda item: order.get(item[0], 99)
                    )
                ],
            )
            for company_id, sources in per_company.items()
        ]
        # Alphabetical, case-blind: this is a list somebody scans for a name they already know.
        rows.sort(key=lambda row: row.company_name.casefold())
        return MarketingClientList(
            rows=rows[:limit], total=len(rows), source_labels=await self._own_source_labels()
        )


#: Private key inside the portal-label map for the locale the defaults translate in — a
#: source is never called this, so it cannot collide with a stored label.
_LOCALE_KEY = "__locale"


def _shape_source_labels(stored: dict | None, locale: str | None) -> dict[str, str]:
    """The stored ``{source: label}`` map with the blanks dropped and the display locale added
    under :data:`_LOCALE_KEY` — one rule for the two statements that read it (the per-company
    metrics read folds it into its settings statement; every other screen reads it alone)."""
    labels = {k: v for k, v in (stored or {}).items() if v}
    labels.setdefault(_LOCALE_KEY, resolve_locale(None, locale))
    return labels


def source_label(source: str, labels: dict[str, str], *, portal: bool) -> str | None:
    """The name a source carries on this caller's screen, or ``None`` for the catalog default.

    The tenant's own label wins for **everyone** — it is the agency's word for its product and
    the marketing page, the client hub and the portal must agree on it. Without one, only an
    external reader gets a substitution (:func:`portal_source_label`'s vendor-free default);
    staff read the catalog name, which the web resolves itself, so the payload says nothing.
    """
    own = labels.get(source)
    if own:
        return own
    return portal_source_label(source, labels) if portal else None


def portal_source_label(source: str, labels: dict[str, str]) -> str:
    """What a client is told a source is called (#446).

    The tenant's own label wins. Without one, a **keyed** source — SE Ranking, Rank Math: the
    agency's supplier, on the agency's account — gets the vendor-free catalog name for what it
    *measures* (``marketing.source.portal.<source>``), because the supplier's name is not the
    client's business and a client's login cannot open it anyway. A **Google** source keeps the
    product name: it is the client's own property and they know it by that name.
    """
    own = labels.get(source)
    if own:
        return own
    locale = labels.get(_LOCALE_KEY)
    if source in PORTAL_NEUTRAL_SOURCES:
        return translate(f"marketing.source.portal.{source}", locale)
    return translate(f"marketing.source.{source}", locale)


#: The sources whose vendor a client is not told about by default (#446): every keyed source —
#: the ones the agency holds the credential for, rather than the client's own Google property.
PORTAL_NEUTRAL_SOURCES = frozenset(
    {MarketingSource.SERANKING.value, MarketingSource.RANKMATH.value}
)


class MarketingSettingsService:
    """Org-level marketing settings (#134): the encrypted Google Ads developer token.

    Mirrors ``GoogleSettingsService`` — the token is write-only (an empty value keeps the stored
    one) and the read reports only whether one is configured, never the value itself.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _row(self) -> MarketingSettings | None:
        return await self.ctx.session.scalar(
            select(MarketingSettings).where(MarketingSettings.org_id == self.ctx.org.id)
        )

    def _read(self, row: MarketingSettings | None) -> MarketingSettingsRead:
        return MarketingSettingsRead(
            ads_developer_token_configured=bool(row and row.ads_developer_token_encrypted),
            env_ads_token_configured=bool(settings.google_ads_developer_token),
            seranking_api_key_configured=bool(row and row.seranking_api_key_encrypted),
            # Always resolved: the settings select has two options and no third "unset" state.
            default_compare=resolve_compare(row.default_compare if row else None),
            # Likewise resolved (#373): a screen showing the house rule shows what a run does,
            # not a form of blanks that each mean "something in the code decides".
            rankings=RankingSettingsRead(
                **parse_rankings(row.rankings if row else None).as_dict()
            ),
            report=ReportSplitSettingsRead(
                **parse_report(row.report if row else None).as_dict()
            ),
            portal_source_labels={
                k: v for k, v in ((row.portal_source_labels if row else None) or {}).items() if v
            },
        )

    async def get(self) -> MarketingSettingsRead:
        return self._read(await self._row())

    @staticmethod
    def _rotated(stored: str | None, submitted: str | None) -> str | None:
        """The ciphertext to store for a write-only secret.

        An empty submission keeps the stored one, and a resent *identical* value is not a
        change — Fernet is randomised, so re-encrypting on every save would rewrite the column
        each time an admin pressed Save on an unrelated field. The Google-client-secret rule,
        now shared by both secrets on this row rather than written out twice (#300).
        """
        if not submitted:
            return stored
        current: str | None = None
        if stored:
            try:
                current = decrypt(stored)
            except ValueError:  # rotated key: the stored secret is dead anyway
                current = None
        return stored if current == submitted else encrypt(submitted)

    async def save(self, data: MarketingSettingsWrite) -> MarketingSettingsRead:
        self.ctx.require("marketing.link.manage")
        row = await self._row()
        ads = self._rotated(
            row.ads_developer_token_encrypted if row else None, data.ads_developer_token
        )
        seranking = self._rotated(
            row.seranking_api_key_encrypted if row else None, data.seranking_api_key
        )
        if row is None:
            row = MarketingSettings(
                org_id=self.ctx.org.id,
                ads_developer_token_encrypted=ads,
                seranking_api_key_encrypted=seranking,
            )
            self.ctx.session.add(row)
        else:
            row.ads_developer_token_encrypted = ads
            row.seranking_api_key_encrypted = seranking
        # Omitted keeps the stored value, like both secrets above — this screen saves every
        # field at once, so a form that could only submit all three would make setting the
        # comparison require retyping a developer token nobody can read back.
        if data.default_compare is not None:
            row.default_compare = data.default_compare.value
        if data.rankings is not None:
            # Merged over what is already stored, not replaced: the same screen saves the whole
            # block at once and a partial payload must not silently reset the fields it omits.
            row.rankings = parse_rankings(
                data.rankings.model_dump(exclude_none=True),
                base=parse_rankings(row.rankings),
            ).as_dict()
        if data.report is not None:
            # Merged for the same reason, and with ``exclude`` deliberately dropped at org level:
            # a link id belongs to one client, so a house rule that could carry one would be a
            # setting that means nothing for every other client on the instance (#381).
            row.report = parse_report(
                {"split": data.report.split.value} if data.report.split else {},
                base=parse_report(row.report),
            ).as_dict()
            row.report.pop("exclude", None)
        if data.portal_source_labels is not None:
            # Merged key by key (#446): the form posts every source it draws, an empty label
            # clears that source back to the default, and a source the form does not name
            # keeps whatever was stored — the same "absent means leave alone" every other
            # field on this row follows.
            merged = dict(row.portal_source_labels or {})
            for source, label in data.portal_source_labels.items():
                if label:
                    merged[source] = label
                else:
                    merged.pop(source, None)
            row.portal_source_labels = merged or None
        await self.ctx.session.flush()
        return self._read(row)


# --- sync (worker side, no request) ---------------------------------------------------------- #
async def sync_link_range(
    session: Any, org: Any, link: MarketingLink, start: date, end: date
) -> None:
    """Fetch ``[start, end]`` for one link and idempotently upsert its daily rows (#133).

    Runs in a worker transaction with the RLS GUC already bound to ``org``. A dead grant flips
    the connection to error and stops *this* link; a plain API error records ``last_error`` on the
    link but never raises, so one broken link never stops the others' sync.
    """
    adapter = source_for(link.source)
    if source_auth(link.source) != AUTH_GOOGLE:
        await _sync_keyed_link(session, org, link, adapter, start, end)
        return
    if link.connection_id is None:
        link.last_error = "errors.google_not_connected"
        return
    connection = await session.get(GoogleConnection, link.connection_id)
    if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
        link.last_error = "errors.google_connection_error"
        return
    if adapter.scope not in set(connection.scopes or []):
        link.last_error = "errors.google_connection_error"
        return
    ads_token = (
        await resolve_ads_developer_token(session, org.id)
        if link.source == MarketingSource.GADS.value
        else None
    )
    try:
        with developer_token_scope(ads_token):
            async with google_client.acting_as(session, org, connection) as gclient:
                daily = await adapter.fetch_daily(
                    gclient, link.external_id, start, end, link.config or {}
                )
    except AdsNotConfigured:
        link.last_error = "marketing.ads_not_configured"
        return
    except Exception as exc:  # noqa: BLE001
        if await google_client.is_oauth_error(exc):
            await google_client.mark_connection_error(session, org, connection, str(exc))
            link.last_error = "errors.google_connection_error"
            return
        detail = google_client.describe_api_error(exc)
        logger.warning(
            "marketing sync failed for link %s (%s): %s",
            link.id,
            await google_client.oauth_client_hint(session, org.id),
            detail or exc,
        )
        # A cause Google named is an i18n key the link card can teach from; anything else keeps
        # Google's own sentence, which is more useful to an admin than the status line was.
        link.last_error = _failure_key(detail, str(detail or exc)[:500], source=link.source)
        return

    await _upsert_daily(session, link, daily)
    link.last_error = None
    link.last_synced_at = datetime.now(UTC)


async def _sync_keyed_link(
    session: Any,
    org: Any,
    link: MarketingLink,
    adapter: Any,
    start: date,
    end: date,
) -> None:
    """Nightly sync for an org-key source (#300).

    Same contract as the Google path and for the same reason: it swallows its own errors and
    records them on the link, so one client's broken SE Ranking project never stops the other
    clients' sync. A missing key is a *configuration* state, not an error — it is what an
    install that has not set one up yet looks like every night, and it must not fill the log.
    """
    try:
        async with keyed_client(
            session, org.id, link.source, link.website_id
        ) as client:
            daily = await adapter.fetch_daily(
                client, link.external_id, start, end, link.config or {}
            )
    except SourceNotConfigured as exc:
        # A *configuration* state, not an error: it is what an install that has not set this
        # source up looks like every night, and it must not fill the log.
        link.last_error = exc.message_key
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("marketing %s sync failed for link %s: %s", link.source, link.id, exc)
        link.last_error = _org_key_error(exc, link.source)
        return
    await _upsert_daily(session, link, daily)
    link.last_error = None
    link.last_synced_at = datetime.now(UTC)


async def _upsert_daily(session: Any, link: MarketingLink, daily: list) -> None:
    """Idempotent per-day upsert keyed on (org_id, link_id, date)."""
    if not daily:
        return
    days = [d.day for d in daily]
    existing = {
        row.date: row
        for row in (
            await session.execute(
                select(MarketingMetricDaily).where(
                    MarketingMetricDaily.org_id == link.org_id,
                    MarketingMetricDaily.link_id == link.id,
                    MarketingMetricDaily.date.in_(days),
                )
            )
        )
        .scalars()
        .all()
    }
    now = datetime.now(UTC)
    for point in daily:
        row = existing.get(point.day)
        if row is None:
            session.add(
                MarketingMetricDaily(
                    org_id=link.org_id,
                    link_id=link.id,
                    date=point.day,
                    metrics=point.metrics,
                    currency=point.currency,
                    synced_at=now,
                )
            )
        else:
            row.metrics = point.metrics
            row.currency = point.currency
            row.synced_at = now
    await session.flush()
