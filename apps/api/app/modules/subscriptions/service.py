"""Business logic for subscriptions (issue #30) — tenant-scoped throughout (Golden Rule 1).

The decisions the issue demanded be made explicitly, made and encoded:

- **Price = append-only history.** ``amount`` lives in ``subscription_prices``; the current
  price is the newest ``valid_from <= today``. A price change appends; past invoices never
  reprice.
- **Included hours measured where budgets are** (#25): consumption is the time logged on the
  *linked projects* in the current period — a bare-table sum over ``time_entries``, the same
  numbers every budget bar draws, never a second bookkeeping of hours.
- **Overage is flagged, not billed** (v1): the usage payload reports it; #31's accounting
  integration decides what to do with it.
- **Proration: unsupported in v1**, on purpose. The first ``subscription.due`` fires on
  ``next_invoice_date`` for a full period.
- **Rollover is stored tenant config** (``RolloverRule``); the carry *computation* lands with
  the invoicing consumer (#31), which owns per-period settlement.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, func, select, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.subscriptions.models import (
    Subscription,
    SubscriptionInterval,
    SubscriptionLine,
    SubscriptionLink,
    SubscriptionPrice,
    SubscriptionStatus,
)
from app.modules.subscriptions.schemas import (
    SubscriptionCreate,
    SubscriptionLineWrite,
    SubscriptionLinkWrite,
    SubscriptionUpdate,
    SubscriptionUsage,
)

ENTITY_TYPE = "subscription"

#: Definition fields the activity trail diffs (§16) — never notes or custom JSONB.
_AUDITED_FIELDS = (
    "name", "status", "company_id", "currency", "interval", "interval_count",
    "start_date", "end_date", "next_invoice_date", "included_hours", "notice_period_days",
)

SORTABLE = {
    "name": func.lower(Subscription.name),
    "status": Subscription.status,
    "next_invoice_date": Subscription.next_invoice_date,
    "start_date": Subscription.start_date,
}

#: Months per interval — the one place the calendar arithmetic lives.
_INTERVAL_MONTHS = {
    SubscriptionInterval.MONTHLY.value: 1,
    SubscriptionInterval.QUARTERLY.value: 3,
    SubscriptionInterval.YEARLY.value: 12,
}


def period_months(interval: str, interval_count: int) -> int:
    return _INTERVAL_MONTHS[interval] * max(1, interval_count)


def add_months(day: date, months: int) -> date:
    """Calendar-safe month addition: 31 Jan + 1 month = 28/29 Feb, never a ValueError."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp to the target month's length.
    next_month_start = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month_start - date.resolution).day
    return date(year, month, min(day.day, last_day))


class SubscriptionService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Subscription)
        self.prices = ctx.repo(SubscriptionPrice)
        self.lines = ctx.repo(SubscriptionLine)
        self.links = ctx.repo(SubscriptionLink)
        self.custom_fields = CustomFieldsService(ctx)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def _org_today(self) -> date:
        """Today in the org's zone (CLAUDE.md §8) — a period boundary is a local concept."""
        return datetime.now(await org_zoneinfo(self.ctx.session, self.ctx.org.id)).date()

    # --- reads --------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        status: str | None = None,
        sort: str | None = None,
    ) -> tuple[Sequence[Subscription], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Subscription.company_id == company_id)
        if status:
            conditions.append(Subscription.status == status)
        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, SORTABLE, default=func.lower(Subscription.name))
        items = list(
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.org_id == self._org_id, *conditions)
            )
            or 0
        )
        await self._attach(items)
        return items, total

    async def get(self, subscription_id: uuid.UUID, *, usage: bool = False) -> Subscription:
        sub = await self.repo.get_or_404(subscription_id)
        await self._attach([sub])
        if usage:
            sub.usage = await self._usage(sub)  # type: ignore[attr-defined]
        return sub

    async def for_company(self, company_id: uuid.UUID) -> Sequence[Subscription]:
        stmt = (
            self.repo.scoped_select()
            .where(Subscription.company_id == company_id)
            .order_by(func.lower(Subscription.name))
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        await self._attach(items)
        return items

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: SubscriptionCreate) -> Subscription:
        self.ctx.require("subscriptions.subscription.write")
        await self._ensure_company(data.company_id)
        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        sub = await self.repo.create(
            company_id=data.company_id,
            name=data.name.strip(),
            status=data.status.value,
            currency=data.currency.upper(),
            interval=data.interval.value,
            interval_count=data.interval_count,
            start_date=data.start_date,
            end_date=data.end_date,
            next_invoice_date=data.next_invoice_date,
            included_hours=data.included_hours,
            rollover=data.rollover.model_dump(),
            notice_period_days=data.notice_period_days,
            notes=data.notes,
            custom=custom,
        )
        # The opening price: history starts at the subscription's own start.
        await self.prices.create(
            subscription_id=sub.id, amount=data.amount, valid_from=data.start_date
        )
        await self._replace_lines(sub.id, data.lines)
        await self._replace_links(sub.id, data.links)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, sub.id)
        await self._attach([sub])
        return sub

    async def update(self, subscription_id: uuid.UUID, data: SubscriptionUpdate) -> Subscription:
        self.ctx.require("subscriptions.subscription.write")
        sub = await self.repo.get_or_404(subscription_id)
        before = snapshot(sub, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        for field in (
            "name", "status", "interval", "interval_count", "start_date", "end_date",
            "next_invoice_date", "included_hours", "notice_period_days", "notes",
        ):
            if field in sent and sent[field] is not None:
                value = sent[field]
                values[field] = value.value if hasattr(value, "value") else value
        if "name" in values:
            values["name"] = values["name"].strip()
        if "company_id" in sent and data.company_id is not None:
            await self._ensure_company(data.company_id)
            values["company_id"] = data.company_id
        if "currency" in sent and data.currency is not None:
            values["currency"] = data.currency.upper()
        if "rollover" in sent and data.rollover is not None:
            values["rollover"] = data.rollover.model_dump()
        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, data.custom or {}
            )

        sub = await self.repo.update(sub, **values)

        # A price change appends to the history — never mutates it (#30's decision).
        if "amount" in sent and data.amount is not None:
            current = await self._current_amount(sub.id)
            if current is None or data.amount != current:
                valid_from = data.amount_valid_from or await self._org_today()
                existing = await self.ctx.session.scalar(
                    self.prices.scoped_select().where(
                        SubscriptionPrice.subscription_id == sub.id,
                        SubscriptionPrice.valid_from == valid_from,
                    )
                )
                if existing is not None:
                    # Same-day correction: today's row is not yet history.
                    await self.prices.update(existing, amount=data.amount)
                else:
                    await self.prices.create(
                        subscription_id=sub.id, amount=data.amount, valid_from=valid_from
                    )

        if data.lines is not None:
            await self._replace_lines(sub.id, data.lines)
        if data.links is not None:
            await self._replace_links(sub.id, data.links)

        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, sub.id, before, snapshot(sub, _AUDITED_FIELDS)
        )
        await self._attach([sub])
        return sub

    async def delete(self, subscription_id: uuid.UUID) -> None:
        self.ctx.require("subscriptions.subscription.delete")
        sub = await self.repo.get_or_404(subscription_id)
        await self.repo.delete(sub)

    async def price_history(self, subscription_id: uuid.UUID) -> Sequence[SubscriptionPrice]:
        await self.repo.get_or_404(subscription_id)
        stmt = (
            self.prices.scoped_select()
            .where(SubscriptionPrice.subscription_id == subscription_id)
            .order_by(SubscriptionPrice.valid_from.desc())
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    # --- Omzet summary --------------------------------------------------------- #
    async def summary(self) -> dict[str, Any]:
        """MRR/ARR over active subscriptions + the invoices due in the next 30 days."""
        subs = list(
            (
                await self.ctx.session.execute(
                    self.repo.scoped_select().where(
                        Subscription.status == SubscriptionStatus.ACTIVE.value
                    )
                )
            )
            .scalars()
            .all()
        )
        await self._attach(subs)
        mrr = sum(
            (s.monthly_equivalent or 0.0)  # type: ignore[attr-defined]
            for s in subs
        )
        today = await self._org_today()
        upcoming = sorted(
            (
                {
                    "subscription_id": s.id,
                    "company_id": s.company_id,
                    "company_name": s.company_name,  # type: ignore[attr-defined]
                    "name": s.name,
                    "next_invoice_date": s.next_invoice_date,
                    "amount": s.amount,  # type: ignore[attr-defined]
                    "currency": s.currency,
                }
                for s in subs
                if s.next_invoice_date is not None
                and s.next_invoice_date <= add_months(today, 1)
            ),
            key=lambda row: row["next_invoice_date"],
        )
        return {
            "mrr": round(mrr, 2),
            "arr": round(mrr * 12, 2),
            "active_count": len(subs),
            "upcoming": upcoming,
        }

    # --- internals ------------------------------------------------------------- #
    async def _replace_lines(
        self, subscription_id: uuid.UUID, lines: list[SubscriptionLineWrite]
    ) -> None:
        for row in await self.ctx.session.scalars(
            self.lines.scoped_select().where(SubscriptionLine.subscription_id == subscription_id)
        ):
            await self.lines.delete(row)
        for index, line in enumerate(lines):
            await self.lines.create(
                subscription_id=subscription_id, position=index, **line.model_dump()
            )

    async def _replace_links(
        self, subscription_id: uuid.UUID, links: list[SubscriptionLinkWrite]
    ) -> None:
        for link in links:
            await self._ensure_link_target(link)
        for row in await self.ctx.session.scalars(
            self.links.scoped_select().where(SubscriptionLink.subscription_id == subscription_id)
        ):
            await self.links.delete(row)
        for link in links:
            await self.links.create(subscription_id=subscription_id, **link.model_dump())

    async def _ensure_company(self, company_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _ensure_link_target(self, link: SubscriptionLinkWrite) -> None:
        """A linked project/task must be this tenant's — a bare table reference (§6)."""
        table = "projects" if link.entity_type == "project" else "tasks"
        ok = await self.ctx.session.scalar(
            text(f"SELECT 1 FROM {table} WHERE id = :eid AND org_id = :oid"),  # noqa: S608
            {"eid": link.entity_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=400,
                fields={"links": "errors.not_found"},
            )

    async def _current_amount(self, subscription_id: uuid.UUID) -> Decimal | None:
        today = await self._org_today()
        return await self.ctx.session.scalar(
            self.prices.scoped_select()
            .where(
                SubscriptionPrice.subscription_id == subscription_id,
                SubscriptionPrice.valid_from <= today,
            )
            .order_by(SubscriptionPrice.valid_from.desc())
            .limit(1)
            .with_only_columns(SubscriptionPrice.amount)
        )

    async def _usage(self, sub: Subscription) -> SubscriptionUsage:
        """Current-period consumption from the linked projects' logged time (#25's numbers)."""
        months = period_months(sub.interval, sub.interval_count)
        period_end = sub.next_invoice_date
        period_start = add_months(period_end, -months) if period_end else None
        project_ids = [
            row.entity_id
            for row in await self.ctx.session.scalars(
                self.links.scoped_select().where(
                    SubscriptionLink.subscription_id == sub.id,
                    SubscriptionLink.entity_type == "project",
                )
            )
        ]
        used = 0.0
        if project_ids and period_start and period_end:
            stmt = text(
                """
                SELECT COALESCE(SUM(minutes), 0) FROM time_entries
                WHERE org_id = :oid AND project_id IN :pids AND ended_at IS NOT NULL
                  AND started_at >= :start AND started_at < :end
                """
            ).bindparams(bindparam("pids", expanding=True))
            minutes = await self.ctx.session.scalar(
                stmt,
                {
                    "oid": self._org_id,
                    "pids": project_ids,
                    "start": period_start,
                    "end": period_end,
                },
            )
            used = round(float(minutes or 0) / 60, 2)
        included = sub.included_hours
        overage = round(max(0.0, used - float(included)), 2) if included is not None else 0.0
        return SubscriptionUsage(
            period_start=period_start,
            period_end=period_end,
            included_hours=included,
            used_hours=used,
            overage_hours=overage,
        )

    async def _attach(self, subs: Sequence[Subscription]) -> None:
        """Batch-resolve company names, current price, lines and links — a list never N+1s."""
        if not subs:
            return
        ids = [s.id for s in subs]
        today = await self._org_today()

        company_rows = (
            await self.ctx.session.execute(
                text("SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"oid": self._org_id, "ids": [s.company_id for s in subs]},
            )
        ).all()
        company_names = {row[0]: row[1] for row in company_rows}

        price_rows = (
            await self.ctx.session.execute(
                self.prices.scoped_select()
                .where(
                    SubscriptionPrice.subscription_id.in_(ids),
                    SubscriptionPrice.valid_from <= today,
                )
                .order_by(
                    SubscriptionPrice.subscription_id, SubscriptionPrice.valid_from.desc()
                )
            )
        ).scalars()
        current: dict[uuid.UUID, Decimal] = {}
        for price in price_rows:
            current.setdefault(price.subscription_id, price.amount)

        line_rows = (
            await self.ctx.session.execute(
                self.lines.scoped_select()
                .where(SubscriptionLine.subscription_id.in_(ids))
                .order_by(SubscriptionLine.position)
            )
        ).scalars()
        lines_by_sub: dict[uuid.UUID, list[SubscriptionLine]] = {}
        for line in line_rows:
            lines_by_sub.setdefault(line.subscription_id, []).append(line)

        link_rows = (
            await self.ctx.session.execute(
                self.links.scoped_select().where(SubscriptionLink.subscription_id.in_(ids))
            )
        ).scalars()
        links_by_sub: dict[uuid.UUID, list[SubscriptionLink]] = {}
        for link in link_rows:
            links_by_sub.setdefault(link.subscription_id, []).append(link)

        for sub in subs:
            amount = current.get(sub.id)
            months = period_months(sub.interval, sub.interval_count)
            sub.company_name = company_names.get(sub.company_id, "")  # type: ignore[attr-defined]
            sub.amount = amount  # type: ignore[attr-defined]
            sub.monthly_equivalent = (  # type: ignore[attr-defined]
                round(float(amount) / months, 2) if amount is not None else None
            )
            sub.lines = lines_by_sub.get(sub.id, [])  # type: ignore[attr-defined]
            sub.links = links_by_sub.get(sub.id, [])  # type: ignore[attr-defined]
