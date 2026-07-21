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
  ``next_invoice_date`` for a full period; left unset, the first activation derives it as
  ``start_date`` + one period (#223).
- **Rollover is stored tenant config** (``RolloverRule``); the carry *computation* lands with
  the invoicing consumer (#31), which owns per-period settlement.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import bindparam, func, select, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.richtext import sanitize_markdown
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
    SubscriptionTemplate,
    SubscriptionType,
)
from app.modules.subscriptions.schemas import (
    PriceIncreaseItem,
    PriceIncreaseRequest,
    PriceIncreaseResult,
    PriceIncreaseTemplateItem,
    SubscriptionCreate,
    SubscriptionLineWrite,
    SubscriptionLinkWrite,
    SubscriptionTemplateCreate,
    SubscriptionTemplateUpdate,
    SubscriptionTypeCreate,
    SubscriptionTypeUpdate,
    SubscriptionUpdate,
    SubscriptionUsage,
)

ENTITY_TYPE = "subscription"

#: Definition fields the activity trail diffs (§16) — never notes or custom JSONB.
_AUDITED_FIELDS = (
    "name", "status", "subscription_type_id", "company_id", "currency", "interval",
    "interval_count", "start_date", "end_date", "next_invoice_date", "included_hours",
    "notice_period_days",
)

#: Starter categories, seeded lazily like ``DEFAULT_LEAVE_TYPES`` — an editable suggestion of
#: what a Dutch agency sells, never law: rename, deactivate or delete freely (#142).
DEFAULT_SUBSCRIPTION_TYPES: list[dict] = [
    {"key": "hosting", "label_i18n": {"nl": "Hosting", "en": "Hosting"}, "position": 10},
    {"key": "onderhoud", "label_i18n": {"nl": "Onderhoud", "en": "Maintenance"}, "position": 20},
    {"key": "marketing", "label_i18n": {"nl": "Marketing", "en": "Marketing"}, "position": 30},
    {"key": "support", "label_i18n": {"nl": "Support", "en": "Support"}, "position": 40},
]

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


@dataclass(frozen=True)
class SubscriptionHours:
    """A covering agreement's hours contribution to a linked project (issue #225).

    Published so `projects` can derive a linked project's budget without importing our
    models (CLAUDE.md §6). ``monthly_hours`` is the monthly equivalent of ``included_hours``
    (the ``monthly_equivalent`` rule the money side already uses), because a project budget
    period has no quarterly/yearly shape to mirror the billing interval with.
    """

    subscription_id: uuid.UUID
    name: str
    included_hours: float
    monthly_hours: float


class SubscriptionService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Subscription)
        self.prices = ctx.repo(SubscriptionPrice)
        self.lines = ctx.repo(SubscriptionLine)
        self.links = ctx.repo(SubscriptionLink)
        self.types = ctx.repo(SubscriptionType)
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
        subscription_type_id: uuid.UUID | None = None,
        sort: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        usage: bool = False,
    ) -> tuple[Sequence[Subscription], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Subscription.company_id == company_id)
        if status:
            conditions.append(Subscription.status == status)
        if subscription_type_id is not None:
            conditions.append(Subscription.subscription_type_id == subscription_type_id)
        if entity_type and entity_id:
            # "Which agreements cover this project/task?" — the project panel's question.
            conditions.append(
                Subscription.id.in_(
                    select(SubscriptionLink.subscription_id).where(
                        SubscriptionLink.org_id == self._org_id,
                        SubscriptionLink.entity_type == entity_type,
                        SubscriptionLink.entity_id == entity_id,
                    )
                )
            )
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
        if usage:
            # One aggregate per row — callers pass this only on entity-filtered lists (a
            # project links to a handful of agreements), never on the 200-row overview.
            for sub in items:
                sub.usage = await self._usage(sub)  # type: ignore[attr-defined]
        return items, total

    async def get(self, subscription_id: uuid.UUID, *, usage: bool = False) -> Subscription:
        sub = await self.repo.get_or_404(subscription_id)
        await self._attach([sub])
        if usage:
            sub.usage = await self._usage(sub)  # type: ignore[attr-defined]
        return sub

    async def hours_for_projects(
        self, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[SubscriptionHours]]:
        """The **active, hours-bearing** agreements covering each project (issue #225).

        A non-empty list is what makes a project's ``budget_hours`` derived and read-only.
        Draft/paused/cancelled agreements, and links without ``included_hours``, source
        nothing. One grouped query for the whole page, never one per row.
        """
        if not project_ids:
            return {}
        stmt = (
            select(
                SubscriptionLink.entity_id,
                Subscription.id,
                Subscription.name,
                Subscription.included_hours,
                Subscription.interval,
                Subscription.interval_count,
            )
            .join(Subscription, Subscription.id == SubscriptionLink.subscription_id)
            .where(
                SubscriptionLink.org_id == self._org_id,
                Subscription.org_id == self._org_id,
                SubscriptionLink.entity_type == "project",
                SubscriptionLink.entity_id.in_(project_ids),
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.included_hours.is_not(None),
            )
            .order_by(func.lower(Subscription.name))
        )
        grouped: dict[uuid.UUID, list[SubscriptionHours]] = {}
        for row in (await self.ctx.session.execute(stmt)).all():
            months = period_months(row[4], row[5])
            grouped.setdefault(row[0], []).append(
                SubscriptionHours(
                    subscription_id=row[1],
                    name=row[2],
                    included_hours=float(row[3]),
                    monthly_hours=round(float(row[3]) / months, 2),
                )
            )
        return grouped

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
        if data.subscription_type_id is not None:
            await self._ensure_type(data.subscription_type_id)
        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        sub = await self.repo.create(
            company_id=data.company_id,
            subscription_type_id=data.subscription_type_id,
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
            # Markdown source (issue #66/#228): raw HTML is stripped on write.
            notes=sanitize_markdown(data.notes),
            custom=custom,
        )
        # The opening price: history starts at the subscription's own start.
        await self.prices.create(
            subscription_id=sub.id, amount=data.amount, valid_from=data.start_date
        )
        await self._replace_lines(sub.id, data.lines)
        await self._replace_links(sub.id, data.links)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, sub.id)
        await self._mark_activated(sub)
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
        if "notes" in values:
            values["notes"] = sanitize_markdown(values["notes"])
        if "company_id" in sent and data.company_id is not None:
            await self._ensure_company(data.company_id)
            values["company_id"] = data.company_id
        if "subscription_type_id" in sent:
            # Explicit null clears the category; a value must be this tenant's.
            if data.subscription_type_id is not None:
                await self._ensure_type(data.subscription_type_id)
            values["subscription_type_id"] = data.subscription_type_id
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
        await self._mark_activated(sub)
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

    # --- bulk price increase --------------------------------------------------- #
    def _bumped(self, current: Decimal, data: PriceIncreaseRequest) -> Decimal:
        if data.mode == "percent":
            new = current * (1 + data.value / 100)
        elif data.mode == "amount":
            new = current + data.value
        else:
            new = data.value
        # Floored at zero: the preview shows the 0,00 rather than the API refusing the batch.
        return max(Decimal("0.00"), new.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    async def price_increase(
        self, data: PriceIncreaseRequest, *, apply: bool
    ) -> PriceIncreaseResult:
        """Compute (and with ``apply`` write) a bulk price change (#30's history model).

        Cancelled agreements are left alone; a subscription that has no price yet on
        ``valid_from`` (it starts later) is skipped rather than given a base of nothing.
        Writes are the manual edit's semantics per row: a row already on ``valid_from`` is
        corrected in place, otherwise the history gains a row — and each written change lands
        on the subscription's activity trail.
        """
        self.ctx.require("subscriptions.subscription.write")
        conditions = [Subscription.status != SubscriptionStatus.CANCELLED.value]
        if data.subscription_type_id is not None:
            await self._ensure_type(data.subscription_type_id)
            conditions.append(Subscription.subscription_type_id == data.subscription_type_id)
        subs = list(
            (
                await self.ctx.session.execute(
                    self.repo.scoped_select()
                    .where(*conditions)
                    .order_by(func.lower(Subscription.name))
                )
            )
            .scalars()
            .all()
        )

        # The base per subscription: the newest price *strictly before* the effective date —
        # so re-running with a corrected value replaces the on-date row instead of
        # compounding on it (the manual edit's same-day-correction semantics). A subscription
        # that starts on the date itself falls back to that opening row as its base.
        base: dict[uuid.UUID, Decimal] = {}
        on_date: dict[uuid.UUID, SubscriptionPrice] = {}
        if subs:
            price_rows = (
                await self.ctx.session.execute(
                    self.prices.scoped_select()
                    .where(
                        SubscriptionPrice.subscription_id.in_([s.id for s in subs]),
                        SubscriptionPrice.valid_from <= data.valid_from,
                    )
                    .order_by(
                        SubscriptionPrice.subscription_id,
                        SubscriptionPrice.valid_from.desc(),
                    )
                )
            ).scalars()
            for price in price_rows:
                if price.valid_from == data.valid_from:
                    on_date[price.subscription_id] = price
                else:
                    base.setdefault(price.subscription_id, price.amount)
            for sub_id, price in on_date.items():
                base.setdefault(sub_id, price.amount)

        company_names: dict[uuid.UUID, str] = {}
        if subs:
            rows = (
                await self.ctx.session.execute(
                    text("SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids")
                    .bindparams(bindparam("ids", expanding=True)),
                    {"oid": self._org_id, "ids": [s.company_id for s in subs]},
                )
            ).all()
            company_names = {row[0]: row[1] for row in rows}

        items: list[PriceIncreaseItem] = []
        activity = ActivityService(self.ctx)
        for sub in subs:
            current = base.get(sub.id)
            if current is None:
                continue
            new = self._bumped(current, data)
            items.append(
                PriceIncreaseItem(
                    subscription_id=sub.id,
                    name=sub.name,
                    company_name=company_names.get(sub.company_id, ""),
                    currency=sub.currency,
                    current_amount=current,
                    new_amount=new,
                )
            )
            if not apply or new == current:
                continue
            existing = on_date.get(sub.id)
            if existing is not None:
                await self.prices.update(existing, amount=new)
            else:
                await self.prices.create(
                    subscription_id=sub.id, amount=new, valid_from=data.valid_from
                )
            await activity.record(
                ENTITY_TYPE,
                sub.id,
                "price_increased",
                {
                    "from": str(current),
                    "to": str(new),
                    "valid_from": data.valid_from.isoformat(),
                },
            )

        templates: list[PriceIncreaseTemplateItem] = []
        if data.include_templates:
            template_repo = self.ctx.repo(SubscriptionTemplate)
            t_conditions = [SubscriptionTemplate.amount.is_not(None)]
            if data.subscription_type_id is not None:
                t_conditions.append(
                    SubscriptionTemplate.subscription_type_id == data.subscription_type_id
                )
            template_rows = list(
                (
                    await self.ctx.session.execute(
                        template_repo.scoped_select()
                        .where(*t_conditions)
                        .order_by(SubscriptionTemplate.position)
                    )
                )
                .scalars()
                .all()
            )
            for template in template_rows:
                new = self._bumped(template.amount, data)
                templates.append(
                    PriceIncreaseTemplateItem(
                        template_id=template.id,
                        name=template.name,
                        current_amount=template.amount,
                        new_amount=new,
                    )
                )
                if apply and new != template.amount:
                    await template_repo.update(template, amount=new)

        return PriceIncreaseResult(items=items, templates=templates)

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

    async def _ensure_type(self, subscription_type_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            self.types.scoped_select()
            .where(SubscriptionType.id == subscription_type_id)
            .with_only_columns(SubscriptionType.id)
        )
        if ok is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=400,
                fields={"subscription_type_id": "errors.validation"},
            )

    async def _mark_activated(self, sub: Subscription) -> None:
        """The *first* transition into ``active``: stamp it and emit ``subscription.activated``.

        The stamp is never cleared, so pause→resume (or cancel→reactivate) fires nothing — the
        tasks module spawns the type's onboarding templates exactly once per agreement (#142).
        Template ids ride in the payload so consumers stay ignorant of this module's tables.

        Also the moment a missing ``next_invoice_date`` is derived (#223): the create form no
        longer asks for one, and an active agreement the cron never sees is a silent billing
        hole. The first cycle boundary is ``start_date`` + one period — the cron's own
        semantics (``subscription.due`` on date X covers ``[X − period, X]``); proration stays
        v1-unsupported, the operator adjusts the date in edit. An end before the first
        boundary leaves it NULL, mirroring the cron's past-the-end rule.
        """
        if sub.status != SubscriptionStatus.ACTIVE.value or sub.activated_at is not None:
            return
        values: dict[str, Any] = {"activated_at": datetime.now(UTC)}
        if sub.next_invoice_date is None:
            next_date = add_months(
                sub.start_date, period_months(sub.interval, sub.interval_count)
            )
            if sub.end_date is None or next_date <= sub.end_date:
                values["next_invoice_date"] = next_date
        await self.repo.update(sub, **values)
        template_ids: list[str] = []
        if sub.subscription_type_id is not None:
            sub_type = await self.ctx.session.scalar(
                self.types.scoped_select().where(
                    SubscriptionType.id == sub.subscription_type_id
                )
            )
            if sub_type is not None:
                template_ids = [str(t) for t in sub_type.task_template_ids]
        await emit(
            "subscription.activated",
            self.ctx,
            {
                "subscription_id": sub.id,
                "company_id": sub.company_id,
                "name": sub.name,
                "subscription_type_id": sub.subscription_type_id,
                "task_template_ids": template_ids,
            },
        )

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
        """Current-period consumption from logged time (#25's numbers): entries on the linked
        projects **or** linked to the subscription itself (the entry form's picker) — one OR
        over two indexed columns, so a project entry that also carries the subscription link
        is never counted twice."""
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
        if period_start and period_end:
            stmt = text(
                """
                SELECT COALESCE(SUM(minutes), 0) FROM time_entries
                WHERE org_id = :oid AND ended_at IS NOT NULL
                  AND (subscription_id = :sid OR project_id IN :pids)
                  AND started_at >= :start AND started_at < :end
                """
            ).bindparams(bindparam("pids", expanding=True))
            minutes = await self.ctx.session.scalar(
                stmt,
                {
                    "oid": self._org_id,
                    "sid": str(sub.id),
                    # An empty IN () is invalid SQL; the impossible id keeps the OR shape.
                    "pids": project_ids or [str(uuid.UUID(int=0))],
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


class SubscriptionTypeService:
    """CRUD for tenant-configurable subscription types (issue #142).

    The contact-types shape: ``label_i18n`` + ``active`` + ``position``, unique ``key`` per
    org, key immutable after create. Deleting a type SET NULLs the subscriptions and templates
    carrying it, so removal never strands a record; deactivating hides it from pickers first.
    A type also carries the task templates spawned on first activation — plain UUIDs into the
    tasks module's table (§6), validated against the bare table on write.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(SubscriptionType)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def list(self, *, include_inactive: bool = False) -> Sequence[SubscriptionType]:
        await self._ensure_default_types()
        stmt = self.repo.scoped_select()
        if not include_inactive:
            stmt = stmt.where(SubscriptionType.active.is_(True))
        stmt = stmt.order_by(SubscriptionType.position, SubscriptionType.key)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _ensure_default_types(self) -> None:
        """Lazy starter set (the ``DEFAULT_LEAVE_TYPES`` pattern): seed once, only for someone
        who could have created them by hand — a read must not write on a reader's behalf."""
        if not self.ctx.can("subscriptions.type.manage"):
            return
        if await self.repo.count() > 0:
            return
        for spec in DEFAULT_SUBSCRIPTION_TYPES:
            await self.repo.create(**spec)

    async def create(self, data: SubscriptionTypeCreate) -> SubscriptionType:
        self.ctx.require("subscriptions.type.manage")
        existing = await self.ctx.session.scalar(
            self.repo.scoped_select()
            .where(SubscriptionType.key == data.key)
            .with_only_columns(SubscriptionType.id)
        )
        if existing is not None:
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"key": "errors.conflict"}
            )
        await self._ensure_task_templates(data.task_template_ids)
        return await self.repo.create(**data.model_dump(mode="json"))

    async def update(
        self, subscription_type_id: uuid.UUID, data: SubscriptionTypeUpdate
    ) -> SubscriptionType:
        self.ctx.require("subscriptions.type.manage")
        sub_type = await self.repo.get_or_404(subscription_type_id)
        if data.task_template_ids is not None:
            await self._ensure_task_templates(data.task_template_ids)
        return await self.repo.update(
            sub_type, **data.model_dump(mode="json", exclude_unset=True)
        )

    async def delete(self, subscription_type_id: uuid.UUID) -> None:
        self.ctx.require("subscriptions.type.manage")
        sub_type = await self.repo.get_or_404(subscription_type_id)
        await self.repo.delete(sub_type)

    async def _ensure_task_templates(self, template_ids: list[uuid.UUID]) -> None:
        """Every referenced task template must be this tenant's — a bare table check (§6)."""
        if not template_ids:
            return
        rows = (
            await self.ctx.session.execute(
                text(
                    "SELECT id FROM task_templates WHERE org_id = :oid AND id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"oid": self._org_id, "ids": list(set(template_ids))},
            )
        ).scalars()
        if set(rows) != set(template_ids):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=400,
                fields={"task_template_ids": "errors.not_found"},
            )


class SubscriptionTemplateService:
    """CRUD for subscription presets (issue #142).

    A template only ever *prefills* the create form — nothing references it afterwards, so it
    deletes freely (no ``active`` dance). Managed under Instellingen, and creatable from a live
    subscription ("save as template"), per the UX template rule.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(SubscriptionTemplate)
        self.types = ctx.repo(SubscriptionType)

    async def list(self) -> Sequence[SubscriptionTemplate]:
        stmt = self.repo.scoped_select().order_by(
            SubscriptionTemplate.position, func.lower(SubscriptionTemplate.name)
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def create(self, data: SubscriptionTemplateCreate) -> SubscriptionTemplate:
        self.ctx.require("subscriptions.template.manage")
        if data.subscription_type_id is not None:
            await self._ensure_type(data.subscription_type_id)
        return await self.repo.create(**self._values(data, data.model_dump()))

    async def update(
        self, template_id: uuid.UUID, data: SubscriptionTemplateUpdate
    ) -> SubscriptionTemplate:
        self.ctx.require("subscriptions.template.manage")
        template = await self.repo.get_or_404(template_id)
        if data.subscription_type_id is not None:
            await self._ensure_type(data.subscription_type_id)
        return await self.repo.update(
            template, **self._values(data, data.model_dump(exclude_unset=True))
        )

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.require("subscriptions.template.manage")
        template = await self.repo.get_or_404(template_id)
        await self.repo.delete(template)

    @staticmethod
    def _values(data: BaseModel, values: dict[str, Any]) -> dict[str, Any]:
        """Column-ready values: scalars stay native (Decimal/UUID), the JSONB blobs go through
        a json-mode dump — ``json.dumps`` cannot serialize a raw Decimal line amount."""
        if values.get("name"):
            values["name"] = values["name"].strip()
        if values.get("notes"):
            # Markdown source (issue #66/#228): raw HTML is stripped on write.
            values["notes"] = sanitize_markdown(values["notes"])
        if values.get("currency"):
            values["currency"] = values["currency"].upper()
        if values.get("interval") is not None:
            values["interval"] = getattr(values["interval"], "value", values["interval"])
        if values.get("rollover") is not None:
            values["rollover"] = data.rollover.model_dump()  # type: ignore[attr-defined]
        if values.get("lines") is not None:
            values["lines"] = [
                line.model_dump(mode="json")
                for line in data.lines  # type: ignore[attr-defined]
            ]
        return values

    async def _ensure_type(self, subscription_type_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            self.types.scoped_select()
            .where(SubscriptionType.id == subscription_type_id)
            .with_only_columns(SubscriptionType.id)
        )
        if ok is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=400,
                fields={"subscription_type_id": "errors.validation"},
            )
