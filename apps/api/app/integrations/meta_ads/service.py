"""Reading and changing a client's Meta ads. Business-licensed — see LICENSE.

**The write spine**, the same one ``google_ads`` runs, in this order and no other:

1. resolve the ad account — 404 outside the caller's tenant or company horizon, never 403;
2. the kill switch;
3. the policy (a call-level refusal raises a 422 naming the field and the limit);
4. the object's own **ownership**, read from Meta: an id arrives from outside, so it is
   resolved only inside *this* account (another account's campaign is a 404, never a call);
5. one write, with the database released — **never retried**;
6. one decision row per applied change.

``validate_only`` runs 1–5 with Meta's own dry-run flag and records nothing.

**Everything is created paused**, because ``status`` is never read from the caller on a
create. Switching something on is :meth:`set_status` with ``ACTIVE``, which asks for a
permission of its own.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, text

from app.core import directory
from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.metagraph import (
    KIND_AD_ACCOUNT,
    KIND_INSTAGRAM,
    KIND_PAGE,
    MetaClient,
    MetaError,
    MetaInvalid,
    MetaNotFound,
)
from app.core.naming import document_name
from app.core.periods import resolve_period
from app.core.timezone import org_today
from app.errors import AppError
from app.integrations.meta.models import MetaAsset, MetaCredential
from app.integrations.meta.service import MetaService, ad_account_path, capabilities
from app.integrations.meta_ads import policy as rules
from app.integrations.meta_ads.models import (
    DecisionKind,
    DecisionSubject,
    MetaAdsDecision,
    MetaAdsPolicy,
    MetaAdsSettings,
)

_SETTINGS_ENTITY = "meta_ads_settings"
_POLICY_ENTITY = "meta_ads_policy"
_POLICY_TRACKED = (
    "max_daily_budget",
    "max_lifetime_budget",
    "max_budget_increase",
    "banned_phrases",
    "dsa_beneficiary",
    "dsa_payor",
)

CAMPAIGN_FIELDS = (
    "id,name,objective,status,effective_status,daily_budget,lifetime_budget,"
    "budget_remaining,bid_strategy,special_ad_categories,start_time,stop_time,"
    "created_time,updated_time"
)
ADSET_FIELDS = (
    "id,campaign_id,name,status,effective_status,daily_budget,lifetime_budget,"
    "budget_remaining,billing_event,optimization_goal,bid_strategy,bid_amount,targeting,"
    "dsa_beneficiary,dsa_payor,start_time,end_time"
)
AD_FIELDS = (
    "id,adset_id,campaign_id,name,status,effective_status,creative{id},issues_info,created_time"
)
CREATIVE_FIELDS = "id,name,status,object_story_id,thumbnail_url"
INSIGHT_FIELDS = (
    "spend,impressions,reach,clicks,ctr,cpc,cpm,actions,date_start,date_stop,"
    "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,account_id,account_name"
)
ACCOUNT_FIELDS = (
    "account_id,name,currency,account_status,disable_reason,amount_spent,spend_cap,balance"
)

#: One read's ceiling. A client with more than this many campaigns is asked for a filter, and
#: the answer says so rather than returning the first page as if it were everything (§17).
MAX_OBJECTS = 500
MAX_INSIGHT_ROWS = 1_000
MAX_INSIGHT_DAYS = 400
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_KIND_SUBJECT = {
    "campaigns": DecisionSubject.CAMPAIGN.value,
    "adsets": DecisionSubject.AD_SET.value,
    "ads": DecisionSubject.AD.value,
}


def _refuse(refusal: rules.Refusal) -> AppError:
    return AppError(
        refusal.code.removeprefix("errors."),
        refusal.code,
        status_code=422,
        fields={refusal.field: refusal.code},
        details=refusal.details,
    )


def _cents(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _stamp(value: Any) -> datetime | None:
    raw = str(value or "")
    if not raw:
        return None
    if len(raw) >= 5 and raw[-5] in "+-" and raw[-3] != ":":
        raw = f"{raw[:-2]}:{raw[-2:]}"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _when(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


class MetaAdsService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.meta = MetaService(ctx)
        self.activity = ActivityService(ctx)

    # --- settings ---------------------------------------------------------------------------- #

    async def settings_row(self, *, create: bool = False) -> MetaAdsSettings | None:
        row = await self.ctx.session.scalar(
            select(MetaAdsSettings).where(MetaAdsSettings.org_id == self.ctx.org.id)
        )
        if row is None and create:
            row = MetaAdsSettings(org_id=self.ctx.org.id)
            self.ctx.session.add(row)
            await self.ctx.session.flush()
        return row

    async def save_settings(self, *, writes_enabled: bool | None) -> MetaAdsSettings:
        self.ctx.require("meta_ads.policy.manage")
        row = await self.settings_row(create=True)
        assert row is not None
        if writes_enabled is not None and writes_enabled != row.writes_enabled:
            row.writes_enabled = writes_enabled
            await self.ctx.session.flush()
            await self.activity.record(
                _SETTINGS_ENTITY,
                row.id,
                "meta_ads.settings_changed",
                {"changed": ["writes_enabled"]},
            )
        return row

    async def require_writes_enabled(self) -> None:
        row = await self.settings_row()
        if row is not None and not row.writes_enabled:
            raise AppError(
                "meta_ads_writes_disabled", "errors.meta_ads_writes_disabled", status_code=409
            )

    # --- accounts ---------------------------------------------------------------------------- #

    async def list_accounts(
        self, *, company_id: uuid.UUID | None = None, active_only: bool = True
    ) -> list[MetaAsset]:
        return await self.meta.list_assets(
            kind=KIND_AD_ACCOUNT, company_id=company_id, active_only=active_only
        )

    async def get_account(self, account_id: uuid.UUID) -> MetaAsset:
        """One ad account, 404 outside the tenant or the company horizon — and 404 for an
        asset that is not an ad account, so the difference reveals nothing (§15)."""
        row = await self.meta.get_asset(account_id)
        if row.kind != KIND_AD_ACCOUNT:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def scopes_for(self, accounts: list[MetaAsset]) -> dict[uuid.UUID, dict[str, bool]]:
        """``{account id: capabilities}`` in one query."""
        ids = {a.credential_id for a in accounts if a.credential_id}
        if not ids:
            return {}
        rows = await self.ctx.session.execute(
            select(MetaCredential.id, MetaCredential.scopes).where(
                MetaCredential.org_id == self.ctx.org.id, MetaCredential.id.in_(ids)
            )
        )
        by_credential = {row[0]: capabilities(row[1] or []) if row[1] else {} for row in rows}
        return {a.id: by_credential.get(a.credential_id, {}) for a in accounts if a.credential_id}

    @asynccontextmanager
    async def open(
        self, account_id: uuid.UUID, *, tool: str = ""
    ) -> AsyncIterator[tuple[MetaClient, MetaAsset]]:
        """A client on the account's credential, with the database released."""
        account = await self.get_account(account_id)
        credentials = await self.meta.credentials_for(account)
        from app.core.metagraph import meta_client

        async with meta_client(credentials, tool=tool) as client, self.ctx.release_db():
            yield client, account

    # --- policy ------------------------------------------------------------------------------ #

    async def policy_rows(
        self, account_id: uuid.UUID | None
    ) -> tuple[MetaAdsPolicy | None, MetaAdsPolicy | None]:
        """``(account row, house row)`` in one statement."""
        condition = (
            or_(MetaAdsPolicy.asset_id.is_(None), MetaAdsPolicy.asset_id == account_id)
            if account_id is not None
            else MetaAdsPolicy.asset_id.is_(None)
        )
        rows = list(
            (
                await self.ctx.session.scalars(
                    self.ctx.repo(MetaAdsPolicy).scoped_select().where(condition)
                )
            ).all()
        )
        own = next((r for r in rows if r.asset_id is not None), None)
        house = next((r for r in rows if r.asset_id is None), None)
        return own, house

    async def policy(self, account_id: uuid.UUID | None) -> rules.AdsPolicy:
        own, house = await self.policy_rows(account_id)
        return rules.resolve(own, house)

    async def save_policy(
        self, account_id: uuid.UUID | None, values: dict[str, Any]
    ) -> MetaAdsPolicy:
        """Upsert one layer. ``values`` holds only what the caller named (§18)."""
        self.ctx.require("meta_ads.policy.manage")
        if account_id is not None:
            await self.get_account(account_id)
        own, house = await self.policy_rows(account_id)
        row = house if account_id is None else own
        if row is None:
            row = MetaAdsPolicy(org_id=self.ctx.org.id, asset_id=account_id)
            self.ctx.session.add(row)
        before = snapshot(row, _POLICY_TRACKED)
        if "max_daily_budget_cents" in values:
            row.max_daily_budget = values["max_daily_budget_cents"]
        if "max_lifetime_budget_cents" in values:
            row.max_lifetime_budget = values["max_lifetime_budget_cents"]
        if "max_budget_increase_pct" in values:
            pct = values["max_budget_increase_pct"]
            row.max_budget_increase = None if pct is None else Decimal(pct) / Decimal(100)
        if "banned_phrases" in values:
            phrases: list[str] = []
            for phrase in values["banned_phrases"] or []:
                clean = " ".join(str(phrase).split())
                if clean and clean.casefold() not in {p.casefold() for p in phrases}:
                    phrases.append(clean[:200])
            row.banned_phrases = phrases
        for key in ("dsa_beneficiary", "dsa_payor"):
            if key in values:
                setattr(row, key, (values[key] or "").strip() or None)
        if "steering" in values:
            row.steering = (values["steering"] or "").strip()
        await self.ctx.session.flush()
        await self.activity.record_update(
            _POLICY_ENTITY, row.id, before, snapshot(row, _POLICY_TRACKED)
        )
        return row

    async def clear_policy(self, account_id: uuid.UUID | None) -> None:
        self.ctx.require("meta_ads.policy.manage")
        own, house = await self.policy_rows(account_id)
        row = house if account_id is None else own
        if row is not None:
            await self.activity.record(_POLICY_ENTITY, row.id, "meta_ads.policy_cleared", {})
            await self.ctx.session.delete(row)
            await self.ctx.session.flush()

    # --- reads ------------------------------------------------------------------------------- #

    async def live_account(self, account_id: uuid.UUID) -> dict[str, Any]:
        async with self.open(account_id, tool="account") as (client, account):
            answer = await client.get(
                ad_account_path(account.external_id), {"fields": ACCOUNT_FIELDS}
            )
            usage = client.stats.usage
        return {
            "account_id": account.id,
            "meta_id": account.external_id,
            "name": str(answer.get("name") or account.name),
            "currency": str(answer.get("currency") or "") or account.currency,
            "account_status": _cents(answer.get("account_status")),
            "disable_reason": _cents(answer.get("disable_reason")),
            "amount_spent_cents": _cents(answer.get("amount_spent")),
            "spend_cap_cents": _cents(answer.get("spend_cap")),
            "balance_cents": _cents(answer.get("balance")),
            "api_tier": usage.ads_tier,
            "usage_percent": usage.percent or None,
        }

    async def list_objects(
        self,
        account_id: uuid.UUID,
        kind: str,
        *,
        statuses: list[str] | None = None,
        campaign_meta_id: str | None = None,
        adset_meta_id: str | None = None,
    ) -> list[dict[str, Any]]:
        fields = {
            "campaigns": CAMPAIGN_FIELDS,
            "adsets": ADSET_FIELDS,
            "ads": AD_FIELDS,
            "adcreatives": CREATIVE_FIELDS,
        }[kind]
        params: dict[str, Any] = {"fields": fields}
        filtering = []
        if statuses:
            params["effective_status"] = [s.upper() for s in statuses]
        if campaign_meta_id:
            filtering.append(
                {"field": "campaign.id", "operator": "IN", "value": [campaign_meta_id]}
            )
        if adset_meta_id:
            filtering.append({"field": "adset.id", "operator": "IN", "value": [adset_meta_id]})
        if filtering:
            params["filtering"] = filtering
        async with self.open(account_id, tool=kind) as (client, account):
            return await client.get_all(
                f"{ad_account_path(account.external_id)}/{kind}",
                params,
                max_items=MAX_OBJECTS,
            )

    async def insights(
        self,
        account_id: uuid.UUID,
        *,
        level: str,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        daily: bool = False,
        campaign_meta_id: str | None = None,
    ) -> dict[str, Any]:
        today = await org_today(self.ctx.session, self.ctx.org.id)
        if date_from is not None and date_to is not None:
            start, end = date_from, min(date_to, today)
            if start > end:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"date_from": "errors.validation"},
                )
            start = max(start, end - timedelta(days=MAX_INSIGHT_DAYS - 1))
        else:
            start, end = resolve_period(period, today, max_days=MAX_INSIGHT_DAYS)
        params: dict[str, Any] = {
            "level": level,
            "fields": INSIGHT_FIELDS,
            "time_range": {"since": start.isoformat(), "until": end.isoformat()},
        }
        if daily:
            params["time_increment"] = 1
        if campaign_meta_id:
            params["filtering"] = [
                {"field": "campaign.id", "operator": "IN", "value": [campaign_meta_id]}
            ]
        warnings: list[str] = []
        totals: dict[str, Any] | None = None
        async with self.open(account_id, tool="insights") as (client, account):
            path = f"{ad_account_path(account.external_id)}/insights"
            try:
                rows = await client.get_all(path, params, max_items=MAX_INSIGHT_ROWS)
            except MetaError as exc:
                if "more than" not in str(exc):
                    raise
                # Over the cap is named, never truncated in silence — and the totals below
                # still cover everything, because they are Meta's own row and not a sum.
                rows = []
                warnings.append("meta_ads.warning.too_many_rows")
            if level == "account" and not daily:
                totals = rows[0] if rows else None
            else:
                try:
                    whole = await client.get(
                        path,
                        {
                            "level": "account",
                            "fields": INSIGHT_FIELDS,
                            "time_range": params["time_range"],
                        },
                    )
                    data = whole.get("data")
                    totals = data[0] if isinstance(data, list) and data else None
                except MetaError:
                    warnings.append("meta_ads.warning.totals_unavailable")
            currency = account.currency
        if end >= today - timedelta(days=2):
            warnings.append("meta_ads.warning.period_not_final")
        return {
            "account_id": account_id,
            "level": level,
            "date_from": start,
            "date_to": end,
            "currency": currency,
            "rows": [_insight(row, level) for row in rows],
            "totals": _insight(totals, "account") if totals else None,
            "warnings": warnings,
        }

    # --- decisions --------------------------------------------------------------------------- #

    async def decisions(
        self,
        account_id: uuid.UUID,
        *,
        subject_meta_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MetaAdsDecision], int]:
        account = await self.get_account(account_id)
        repo = self.ctx.repo(MetaAdsDecision)
        conditions = [MetaAdsDecision.asset_id == account.id]
        if subject_meta_id:
            conditions.append(MetaAdsDecision.subject_id == subject_meta_id)
        rows = list(
            (
                await self.ctx.session.scalars(
                    repo.scoped_select()
                    .where(*conditions)
                    .order_by(MetaAdsDecision.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        total = int(
            await self.ctx.session.scalar(repo.scoped_count_select().where(*conditions)) or 0
        )
        return rows, total

    async def record_kept(
        self, account_id: uuid.UUID, *, subject_type: str, subject_meta_id: str, reason: str
    ) -> MetaAdsDecision:
        """A judgement that changed nothing. Rides the campaign key: a key that may change a
        campaign may certainly record that it chose not to."""
        self.ctx.require("meta_ads.campaign.write")
        kind = {"campaign": "campaigns", "ad_set": "adsets", "ad": "ads"}[subject_type]
        async with self.open(account_id, tool="kept") as (client, account):
            current = await self._owned(client, account, subject_meta_id, kind)
        return await self._decide(
            account,
            subject_type=subject_type,
            subject_id=subject_meta_id,
            subject_name=str(current.get("name") or ""),
            decision=DecisionKind.KEPT.value,
            reason=reason,
            applied=False,
            payload={},
        )

    # --- writes: campaigns -------------------------------------------------------------------- #

    async def create_campaign(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        effective = await self.policy(account.id)
        refusal = rules.political_refusal(
            list(payload.special_ad_categories),
            list(payload.special_ad_category_countries) or ["NL"],
        )
        if refusal is None:
            refusal = rules.budget_refusal(
                effective, kind="daily", new=payload.daily_budget_cents, previous=None
            ) or rules.budget_refusal(
                effective, kind="lifetime", new=payload.lifetime_budget_cents, previous=None
            )
        if refusal is not None:
            raise _refuse(refusal)
        if payload.daily_budget_cents or payload.lifetime_budget_cents:
            self.ctx.require("meta_ads.budget.write")
        has_budget = bool(payload.daily_budget_cents or payload.lifetime_budget_cents)
        body: dict[str, Any] = {
            "name": payload.name,
            "objective": payload.objective,
            "status": "PAUSED",
            "special_ad_categories": list(payload.special_ad_categories),
            "daily_budget": payload.daily_budget_cents,
            "lifetime_budget": payload.lifetime_budget_cents,
            "bid_strategy": payload.bid_strategy,
            "start_time": _when(payload.start_time),
            "stop_time": _when(payload.stop_time),
        }
        if payload.special_ad_category_countries:
            body["special_ad_category_country"] = [
                c.upper() for c in payload.special_ad_category_countries
            ]
        if not has_budget:
            # Required since v24 when the budget is set on the ad sets: whether they may
            # share it. Stated, not defaulted by omission.
            body["is_adset_budget_sharing_enabled"] = False
        return await self._create(
            account,
            "campaigns",
            body,
            name=payload.name,
            validate_only=payload.validate_only,
            reason=payload.reason,
            record={
                "objective": payload.objective,
                "daily_budget_cents": payload.daily_budget_cents,
                "lifetime_budget_cents": payload.lifetime_budget_cents,
            },
        )

    async def update_object(
        self, account_id: uuid.UUID, kind: str, meta_id: str, payload: Any
    ) -> dict[str, Any]:
        """Rename, reschedule, retarget, pause or archive. Never activate, never a budget."""
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        values = payload.model_dump(exclude_unset=True, exclude={"validate_only", "reason"})
        body: dict[str, Any] = {}
        for key in ("name", "status"):
            if values.get(key) is not None:
                body[key] = values[key]
        for key, target in (
            ("start_time", "start_time"),
            ("stop_time", "stop_time"),
            ("end_time", "end_time"),
        ):
            if values.get(key) is not None:
                body[target] = _when(values[key])
        if values.get("bid_amount_cents") is not None:
            body["bid_amount"] = values["bid_amount_cents"]
        if values.get("creative_meta_id"):
            body["creative"] = {"creative_id": values["creative_meta_id"]}
        for key in ("dsa_beneficiary", "dsa_payor"):
            if values.get(key):
                body[key] = values[key]
        if values.get("targeting") is not None:
            body["targeting"] = _targeting(payload.targeting)
        if not body:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"name": "errors.meta_ads_nothing_to_change"},
            )
        decision = DecisionKind.UPDATED.value
        if body.get("status") == "PAUSED":
            decision = DecisionKind.PAUSED.value
        elif body.get("status") == "ARCHIVED":
            decision = DecisionKind.ARCHIVED.value
        return await self._change(
            account,
            kind,
            meta_id,
            body,
            decision=decision,
            validate_only=payload.validate_only,
            reason=payload.reason,
            record={"changed": sorted(body)},
        )

    async def set_budget(
        self, account_id: uuid.UUID, kind: str, meta_id: str, payload: Any
    ) -> dict[str, Any]:
        self.ctx.require("meta_ads.budget.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        if (payload.daily_budget_cents is None) == (payload.lifetime_budget_cents is None):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"daily_budget_cents": "errors.meta_ads_one_budget"},
            )
        which = "daily" if payload.daily_budget_cents is not None else "lifetime"
        new = payload.daily_budget_cents or payload.lifetime_budget_cents
        effective = await self.policy(account.id)
        changes = await self._recent_budget_changes(account.id, meta_id)
        if changes >= rules.BUDGET_CHANGES_PER_HOUR and not payload.validate_only:
            raise AppError(
                "meta_ads_budget_changes",
                "errors.meta_ads_budget_changes",
                status_code=429,
                fields={f"{which}_budget_cents": "errors.meta_ads_budget_changes"},
                details={"limit": rules.BUDGET_CHANGES_PER_HOUR, "per": "hour"},
            )

        def judge(current: dict[str, Any]) -> None:
            previous = _cents(current.get(f"{which}_budget"))
            refusal = rules.budget_refusal(effective, kind=which, new=new, previous=previous)
            if refusal is not None:
                raise _refuse(refusal)

        return await self._change(
            account,
            kind,
            meta_id,
            {f"{which}_budget": new},
            decision=DecisionKind.BUDGET_CHANGED.value,
            validate_only=payload.validate_only,
            reason=payload.reason,
            judge=judge,
            record_from=lambda current: {
                "budget": which,
                "from_cents": _cents(current.get(f"{which}_budget")),
                "to_cents": new,
            },
        )

    async def set_status(
        self, account_id: uuid.UUID, kind: str, meta_id: str, status: str, payload: Any
    ) -> dict[str, Any]:
        """Switch on, or pause. ``ACTIVE`` is the act that spends and asks for its own key."""
        self.ctx.require(
            "meta_ads.ads.activate" if status == "ACTIVE" else "meta_ads.campaign.write"
        )
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        return await self._change(
            account,
            kind,
            meta_id,
            {"status": status},
            decision=(
                DecisionKind.ACTIVATED.value if status == "ACTIVE" else DecisionKind.PAUSED.value
            ),
            validate_only=payload.validate_only,
            reason=payload.reason,
            record_from=lambda current: {"from": current.get("status"), "to": status},
        )

    # --- writes: ad sets, creatives, ads ------------------------------------------------------ #

    async def create_adset(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        if payload.daily_budget_cents or payload.lifetime_budget_cents:
            self.ctx.require("meta_ads.budget.write")
        if payload.lifetime_budget_cents and payload.end_time is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"end_time": "errors.meta_ads_end_time_required"},
            )
        effective = await self.policy(account.id)
        refusal = rules.budget_refusal(
            effective, kind="daily", new=payload.daily_budget_cents, previous=None
        ) or rules.budget_refusal(
            effective, kind="lifetime", new=payload.lifetime_budget_cents, previous=None
        )
        if refusal is not None:
            raise _refuse(refusal)
        beneficiary, payor = await self._dsa(account, effective, payload)
        body: dict[str, Any] = {
            "name": payload.name,
            "campaign_id": payload.campaign_meta_id,
            "status": "PAUSED",
            "optimization_goal": payload.optimization_goal,
            "billing_event": payload.billing_event,
            "daily_budget": payload.daily_budget_cents,
            "lifetime_budget": payload.lifetime_budget_cents,
            "bid_strategy": payload.bid_strategy,
            "bid_amount": payload.bid_amount_cents,
            "targeting": _targeting(payload.targeting),
            "destination_type": payload.destination_type,
            "promoted_object": payload.promoted_object,
            "start_time": _when(payload.start_time),
            "end_time": _when(payload.end_time),
            "dsa_beneficiary": beneficiary,
            "dsa_payor": payor,
        }
        return await self._create(
            account,
            "adsets",
            body,
            name=payload.name,
            validate_only=payload.validate_only,
            reason=payload.reason,
            parents={"campaigns": payload.campaign_meta_id},
            record={
                "campaign_meta_id": payload.campaign_meta_id,
                "daily_budget_cents": payload.daily_budget_cents,
                "lifetime_budget_cents": payload.lifetime_budget_cents,
                "countries": [c.upper() for c in payload.targeting.countries],
            },
        )

    async def create_creative(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        effective = await self.policy(account.id)
        refusal = rules.phrase_refusal(
            effective,
            {
                "message": payload.message,
                "headline": payload.headline,
                "description": payload.description,
            },
        )
        if refusal is not None:
            raise _refuse(refusal)
        page = await self._sibling(account, payload.page_id, KIND_PAGE, "page_id")
        instagram = None
        if payload.instagram_id is not None:
            instagram = await self._sibling(
                account, payload.instagram_id, KIND_INSTAGRAM, "instagram_id"
            )
        body: dict[str, Any] = {"name": payload.name}
        if payload.meta_post_id:
            if not payload.meta_post_id.startswith(f"{page.external_id}_"):
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"meta_post_id": "errors.meta_ads_post_not_this_page"},
                )
            body["object_story_id"] = payload.meta_post_id
        else:
            if not payload.link:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"link": "errors.required"},
                )
            link_data: dict[str, Any] = {
                "link": payload.link,
                "message": payload.message,
                "name": payload.headline,
                "description": payload.description,
                "image_hash": payload.image_hash,
                "picture": payload.image_url if not payload.image_hash else None,
            }
            if payload.call_to_action:
                link_data["call_to_action"] = {
                    "type": payload.call_to_action,
                    "value": {"link": payload.link},
                }
            body["object_story_spec"] = {
                "page_id": page.external_id,
                "link_data": {k: v for k, v in link_data.items() if v is not None},
            }
            if instagram is not None:
                # ``instagram_actor_id`` was removed in 2025; this is its replacement.
                body["object_story_spec"]["instagram_user_id"] = instagram.external_id
        return await self._create(
            account,
            "adcreatives",
            body,
            name=payload.name,
            validate_only=False,
            reason=payload.reason,
            subject=DecisionSubject.CREATIVE.value,
            record={"page": page.name, "meta_post_id": payload.meta_post_id},
        )

    async def create_ad(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        body = {
            "name": payload.name,
            "adset_id": payload.adset_meta_id,
            "creative": {"creative_id": payload.creative_meta_id},
            "status": "PAUSED",
        }
        return await self._create(
            account,
            "ads",
            body,
            name=payload.name,
            validate_only=payload.validate_only,
            reason=payload.reason,
            parents={"adsets": payload.adset_meta_id, "adcreatives": payload.creative_meta_id},
            record={
                "adset_meta_id": payload.adset_meta_id,
                "creative_meta_id": payload.creative_meta_id,
            },
        )

    async def upload_image(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        self.ctx.require("meta_ads.campaign.write")
        account = await self.get_account(account_id)
        await self.require_writes_enabled()
        try:
            raw = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"content_base64": "errors.validation"},
            ) from None
        if len(raw) > MAX_IMAGE_BYTES:
            raise AppError(
                "validation",
                "errors.upload_too_large",
                status_code=413,
                fields={"content_base64": "errors.upload_too_large"},
                details={"limit_bytes": MAX_IMAGE_BYTES},
            )
        credentials = await self.meta.credentials_for(account)
        from app.core.metagraph import meta_client

        async with meta_client(credentials, tool="image") as client, self.ctx.release_db():
            answer = await client.post(
                f"{ad_account_path(account.external_id)}/adimages",
                {},
                files={"filename": (payload.filename, raw, "application/octet-stream")},
            )
        images = answer.get("images")
        first = next(iter(images.values()), {}) if isinstance(images, dict) and images else {}
        digest = str(first.get("hash") or "")
        if not digest:
            raise MetaInvalid("meta accepted the image and returned no hash")
        return {"hash": digest, "url": str(first.get("url") or "") or None}

    async def boost(self, account_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        """Four creates in a row, each paused. **Not atomic**, and says so.

        Meta has no transaction, so a refusal at step three leaves steps one and two made.
        They are paused and named in the answer (``failed_step``) rather than cleaned up: a
        delete that itself fails would leave the caller knowing less than this does.
        """
        from types import SimpleNamespace

        self.ctx.require("meta_ads.campaign.write")
        self.ctx.require("meta_ads.budget.write")
        name = (payload.name or "").strip() or f"Boost {payload.meta_post_id}"
        made: dict[str, str | None] = {
            "campaign_meta_id": None,
            "adset_meta_id": None,
            "creative_meta_id": None,
            "ad_meta_id": None,
        }
        step = "campaign"
        try:
            campaign = await self.create_campaign(
                account_id,
                SimpleNamespace(
                    name=name,
                    objective=payload.objective,
                    special_ad_categories=[],
                    special_ad_category_countries=[],
                    daily_budget_cents=None,
                    lifetime_budget_cents=None,
                    bid_strategy=None,
                    start_time=None,
                    stop_time=None,
                    validate_only=payload.validate_only,
                    reason=payload.reason,
                ),
            )
            if payload.validate_only:
                return {"applied": False, "validate_only": True, **made, "failed_step": None}
            made["campaign_meta_id"] = campaign["meta_id"]
            step = "ad_set"
            adset = await self.create_adset(
                account_id,
                SimpleNamespace(
                    campaign_meta_id=campaign["meta_id"],
                    name=name,
                    optimization_goal=payload.optimization_goal,
                    billing_event="IMPRESSIONS",
                    daily_budget_cents=payload.daily_budget_cents,
                    lifetime_budget_cents=None,
                    bid_strategy=None,
                    bid_amount_cents=None,
                    targeting=payload.targeting,
                    destination_type=None,
                    promoted_object=None,
                    start_time=payload.start_time,
                    end_time=payload.end_time,
                    dsa_beneficiary=payload.dsa_beneficiary,
                    dsa_payor=payload.dsa_payor,
                    validate_only=False,
                    reason=payload.reason,
                ),
            )
            made["adset_meta_id"] = adset["meta_id"]
            step = "creative"
            creative = await self.create_creative(
                account_id,
                SimpleNamespace(
                    name=name,
                    page_id=payload.page_id,
                    instagram_id=None,
                    meta_post_id=payload.meta_post_id,
                    link=None,
                    message=None,
                    headline=None,
                    description=None,
                    image_hash=None,
                    image_url=None,
                    call_to_action=None,
                    reason=payload.reason,
                ),
            )
            made["creative_meta_id"] = creative["meta_id"]
            step = "ad"
            ad = await self.create_ad(
                account_id,
                SimpleNamespace(
                    adset_meta_id=adset["meta_id"],
                    name=name,
                    creative_meta_id=creative["meta_id"],
                    validate_only=False,
                    reason=payload.reason,
                ),
            )
            made["ad_meta_id"] = ad["meta_id"]
        except (AppError, MetaError):
            if made["campaign_meta_id"] is None:
                raise
            return {"applied": True, "validate_only": False, **made, "failed_step": step}
        return {"applied": True, "validate_only": False, **made, "failed_step": None}

    # --- internals --------------------------------------------------------------------------- #

    async def _create(
        self,
        account: MetaAsset,
        kind: str,
        body: dict[str, Any],
        *,
        name: str,
        validate_only: bool,
        reason: str,
        record: dict[str, Any],
        parents: dict[str, str] | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        if validate_only:
            body = {**body, "execution_options": ["validate_only"]}
        credentials = await self.meta.credentials_for(account)
        from app.core.metagraph import meta_client

        async with meta_client(credentials, tool=f"create:{kind}") as client, self.ctx.release_db():
            for parent_kind, parent_id in (parents or {}).items():
                await self._owned(client, account, parent_id, parent_kind)
            answer = await client.post(f"{ad_account_path(account.external_id)}/{kind}", body)
        meta_id = str(answer.get("id") or "") or None
        if validate_only:
            return {
                "meta_id": None,
                "kind": kind,
                "applied": False,
                "validate_only": True,
                "status": None,
                "decision_id": None,
            }
        if meta_id is None:
            raise MetaInvalid("meta accepted the create and returned no id")
        decision = await self._decide(
            account,
            subject_type=subject or _KIND_SUBJECT.get(kind, kind),
            subject_id=meta_id,
            subject_name=name,
            decision=DecisionKind.CREATED.value,
            reason=reason,
            applied=True,
            payload=record,
        )
        return {
            "meta_id": meta_id,
            "kind": kind,
            "applied": True,
            "validate_only": False,
            "status": body.get("status"),
            "decision_id": decision.id,
        }

    async def _change(
        self,
        account: MetaAsset,
        kind: str,
        meta_id: str,
        body: dict[str, Any],
        *,
        decision: str,
        validate_only: bool,
        reason: str,
        record: dict[str, Any] | None = None,
        record_from: Any = None,
        judge: Any = None,
    ) -> dict[str, Any]:
        if validate_only:
            body = {**body, "execution_options": ["validate_only"]}
        credentials = await self.meta.credentials_for(account)
        refused: AppError | None = None
        current: dict[str, Any] = {}
        from app.core.metagraph import meta_client

        async with meta_client(credentials, tool=f"change:{kind}") as client, self.ctx.release_db():
            current = await self._owned(client, account, meta_id, kind)
            if judge is not None:
                try:
                    judge(current)
                except AppError as exc:
                    refused = exc
            if refused is None:
                await client.post(meta_id, body)
        if refused is not None:
            raise refused
        payload = dict(record or {})
        if record_from is not None:
            payload.update(record_from(current))
        status = body.get("status") or current.get("status")
        if validate_only:
            return {
                "meta_id": meta_id,
                "kind": kind,
                "applied": False,
                "validate_only": True,
                "status": str(current.get("status") or "") or None,
                "decision_id": None,
            }
        row = await self._decide(
            account,
            subject_type=_KIND_SUBJECT.get(kind, kind),
            subject_id=meta_id,
            subject_name=str(body.get("name") or current.get("name") or ""),
            decision=decision,
            reason=reason,
            applied=True,
            payload=payload,
        )
        return {
            "meta_id": meta_id,
            "kind": kind,
            "applied": True,
            "validate_only": False,
            "status": str(status) if status else None,
            "decision_id": row.id,
        }

    async def _owned(
        self, client: MetaClient, account: MetaAsset, meta_id: str, kind: str
    ) -> dict[str, Any]:
        """The object, **if it is this account's**. Anything else is a 404.

        The safety property is the lookup, not the permission (Cloudflare's redirect rule):
        the id arrives from outside, so it is resolved against the account named in the path
        before anything is sent to it. Another account's campaign — another client's — and
        an id that is not a campaign at all answer the same way.
        """
        clean = str(meta_id or "").strip()
        if not clean.isdigit():
            raise AppError("not_found", "errors.not_found", status_code=404)
        fields = "id,account_id,name,status,effective_status"
        if kind in ("campaigns", "adsets"):
            fields += ",daily_budget,lifetime_budget"
        try:
            answer = await client.get(clean, {"fields": fields})
        except (MetaNotFound, MetaInvalid):
            raise AppError("not_found", "errors.not_found", status_code=404) from None
        owner = str(answer.get("account_id") or "").removeprefix("act_")
        if owner != account.external_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return answer

    async def _sibling(
        self, account: MetaAsset, asset_id: uuid.UUID, kind: str, field: str
    ) -> MetaAsset:
        """A Page or Instagram account an ad may run as: visible, of that kind, and the same
        client's as the ad account — an ad for one client never runs as another's Page."""
        row = await self.ctx.session.scalar(
            self.ctx.repo(MetaAsset).scoped_select().where(MetaAsset.id == asset_id)
        )
        if row is None or row.kind != kind or row.company_id != account.company_id:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={field: "errors.meta_ads_asset_mismatch"},
            )
        return row

    async def _dsa(
        self, account: MetaAsset, effective: rules.AdsPolicy, payload: Any
    ) -> tuple[str | None, str | None]:
        """The two names the DSA wants, or a refusal naming the one that nobody stated."""
        countries = rules.eu_countries(payload.targeting.countries)
        if not countries:
            return payload.dsa_beneficiary, payload.dsa_payor
        client_name = None
        if account.company_id is not None:
            row = (
                await self.ctx.session.execute(
                    text("SELECT name, legal_name FROM companies WHERE org_id = :org AND id = :id"),
                    {"org": str(self.ctx.org.id), "id": str(account.company_id)},
                )
            ).first()
            if row is not None:
                client_name = document_name(row[0], row[1]) or None
        beneficiary, payor = rules.dsa_names(
            requested_beneficiary=payload.dsa_beneficiary,
            requested_payor=payload.dsa_payor,
            policy=effective,
            account_beneficiary=account.dsa_beneficiary,
            account_payor=account.dsa_payor,
            client_name=client_name,
        )
        missing = {
            key: "errors.meta_ads_dsa_required"
            for key, value in (("dsa_beneficiary", beneficiary), ("dsa_payor", payor))
            if not value
        }
        if missing:
            raise AppError(
                "meta_ads_dsa_required",
                "errors.meta_ads_dsa_required",
                status_code=422,
                fields=missing,
                details={"countries": countries},
            )
        return beneficiary, payor

    async def _recent_budget_changes(self, account_id: uuid.UUID, meta_id: str) -> int:
        since = datetime.now(UTC) - timedelta(hours=1)
        return int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(MetaAdsDecision)
                .where(
                    MetaAdsDecision.org_id == self.ctx.org.id,
                    MetaAdsDecision.asset_id == account_id,
                    MetaAdsDecision.subject_id == meta_id,
                    MetaAdsDecision.decision == DecisionKind.BUDGET_CHANGED.value,
                    MetaAdsDecision.applied.is_(True),
                    MetaAdsDecision.created_at >= since,
                )
            )
            or 0
        )

    async def _decide(
        self,
        account: MetaAsset,
        *,
        subject_type: str,
        subject_id: str,
        subject_name: str,
        decision: str,
        reason: str,
        applied: bool,
        payload: dict[str, Any],
    ) -> MetaAdsDecision:
        system = getattr(self.ctx, "is_system", False)
        user = self.ctx.user
        impersonator = getattr(self.ctx, "impersonated_by", None)
        row = MetaAdsDecision(
            org_id=self.ctx.org.id,
            asset_id=account.id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_name=subject_name[:255],
            decision=decision,
            reason=(reason or "").strip(),
            applied=applied,
            payload=payload,
            decided_by_user_id=None if system else getattr(user, "id", None),
            decided_by_name=""
            if system
            else str(getattr(user, "full_name", None) or getattr(user, "email", "") or ""),
            impersonator_name=(
                str(
                    getattr(impersonator, "full_name", None)
                    or getattr(impersonator, "email", "")
                    or ""
                )
                or None
            )
            if impersonator is not None
            else None,
        )
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        await self.ctx.session.refresh(row)
        return row

    async def company_names(self, accounts: list[MetaAsset]) -> dict[uuid.UUID, str]:
        return await directory.labels_for(self.ctx, "company", (a.company_id for a in accounts))


def _targeting(targeting: Any) -> dict[str, Any]:
    """Our targeting shape as Meta's. What ``extra`` names wins, because it is the caller
    spelling Meta's own vocabulary — except the audience flag, which is always stated."""
    body: dict[str, Any] = {
        "geo_locations": {"countries": [c.upper() for c in targeting.countries]},
        "age_min": targeting.age_min,
        "age_max": targeting.age_max,
    }
    if targeting.genders:
        body["genders"] = list(targeting.genders)
    body.update(targeting.extra or {})
    body["targeting_automation"] = {
        **(body.get("targeting_automation") or {}),
        "advantage_audience": 1 if targeting.advantage_audience else 0,
    }
    return body


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _insight(row: dict[str, Any] | None, level: str) -> dict[str, Any]:
    """One insights row, as numbers. Meta sends every figure as a string."""
    row = row or {}
    prefix = {"campaign": "campaign", "adset": "adset", "ad": "ad"}.get(level, "account")
    actions: dict[str, float] = {}
    for entry in row.get("actions") or ():
        if isinstance(entry, dict) and entry.get("action_type"):
            actions[str(entry["action_type"])] = _number(entry.get("value"))
    reach = _optional(row.get("reach"))

    def day(key: str) -> date | None:
        try:
            return date.fromisoformat(str(row.get(key) or ""))
        except ValueError:
            return None

    return {
        "meta_id": str(row.get(f"{prefix}_id") or "") or None,
        "name": str(row.get(f"{prefix}_name") or "") or None,
        "date_from": day("date_start"),
        "date_to": day("date_stop"),
        "spend": _number(row.get("spend")),
        "impressions": int(_number(row.get("impressions"))),
        "reach": int(reach) if reach is not None else None,
        "clicks": int(_number(row.get("clicks"))),
        "ctr": _optional(row.get("ctr")),
        "cpc": _optional(row.get("cpc")),
        "cpm": _optional(row.get("cpm")),
        "actions": actions,
    }


__all__ = ["MetaAdsService", "_cents", "_stamp"]
