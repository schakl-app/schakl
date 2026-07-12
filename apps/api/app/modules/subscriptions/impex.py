"""CSV import/export shape for subscriptions (issue #77, settings hub round).

Upsert matches on ``name``. ``amount`` is the price valid today: on create it seeds the
price history, on update the service appends a new price row when it changed — exactly the
form's behaviour, because the import goes through the same service.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.modules.subscriptions.models import (
    Subscription,
    SubscriptionInterval,
    SubscriptionStatus,
    SubscriptionType,
)
from app.modules.subscriptions.schemas import SubscriptionCreate, SubscriptionUpdate
from app.modules.subscriptions.service import SubscriptionService

_FIELDS = (
    "name", "end_date", "next_invoice_date", "included_hours", "notes", "company_id",
    "subscription_type_id",
)


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await SubscriptionService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=filters.get("company_id"),
        status=filters.get("status"),
        sort=filters.get("sort"),
    )
    # _attach carries company_name + current amount; the type key rides along for the
    # export getter — one grouped query, keys because labels are per-locale tenant data.
    type_ids = {s.subscription_type_id for s in items if s.subscription_type_id is not None}
    if type_ids:
        rows = await ctx.session.execute(
            ctx.repo(SubscriptionType)
            .scoped_select()
            .where(SubscriptionType.id.in_(type_ids))
            .with_only_columns(SubscriptionType.id, SubscriptionType.key)
        )
        keys = dict(rows.all())
        for sub in items:
            sub.subscription_type_key = keys.get(sub.subscription_type_id)  # type: ignore[attr-defined]
    return items


async def _resolve_type(ctx: RequestContext, refs: list[str]) -> dict[str, uuid.UUID | str]:
    """Type references resolve by ``key`` (or UUID) — types have no ``name`` column, and the
    per-locale labels are ambiguous across locales. Keys are org-unique, so never ambiguous."""
    by_id: dict[str, uuid.UUID] = {}
    keys: list[str] = []
    for ref in refs:
        try:
            by_id[ref] = uuid.UUID(ref)
        except ValueError:
            keys.append(ref)

    repo = ctx.repo(SubscriptionType)
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        rows = (
            await ctx.session.execute(
                repo.scoped_select()
                .where(SubscriptionType.id.in_(by_id.values()))
                .with_only_columns(SubscriptionType.id)
            )
        ).scalars()
        found = set(rows)
        for ref, ref_id in by_id.items():
            resolved[ref] = ref_id if ref_id in found else "impex.errors.unresolved_reference"
    if keys:
        rows = await ctx.session.execute(
            repo.scoped_select()
            .where(SubscriptionType.key.in_(keys))
            .with_only_columns(SubscriptionType.key, SubscriptionType.id)
        )
        by_key = dict(rows.all())
        for key in keys:
            resolved[key] = by_key.get(key, "impex.errors.unresolved_reference")
    return resolved


async def _find_existing(ctx: RequestContext, values: list[str]) -> dict[str, list[Any]]:
    stmt = ctx.repo(Subscription).scoped_select().where(Subscription.name.in_(values))
    found: dict[str, list[Any]] = {}
    for sub in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(sub.name, []).append(sub)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> None:
    await SubscriptionService(ctx).create(
        SubscriptionCreate(
            name=values["name"],
            company_id=values["company_id"],
            amount=Decimal(values.get("amount") or "0"),
            status=SubscriptionStatus(values["status"])
            if values.get("status")
            else SubscriptionStatus.DRAFT,
            interval=SubscriptionInterval(values["interval"])
            if values.get("interval")
            else SubscriptionInterval.MONTHLY,
            subscription_type_id=values.get("subscription_type_id"),
            start_date=values["start_date"],
            end_date=values.get("end_date"),
            next_invoice_date=values.get("next_invoice_date"),
            included_hours=values.get("included_hours"),
            notes=values.get("notes"),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, sub: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _FIELDS if key in values}
    if values.get("status"):
        fields["status"] = SubscriptionStatus(values["status"])
    if values.get("interval"):
        fields["interval"] = SubscriptionInterval(values["interval"])
    if values.get("start_date"):
        fields["start_date"] = values["start_date"]
    if values.get("amount"):
        fields["amount"] = Decimal(values["amount"])
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await SubscriptionService(ctx).update(sub.id, SubscriptionUpdate(**fields))


SUBSCRIPTION_IMPEX = ImpexDescriptor(
    entity_type="subscription",
    read_permission="subscriptions.subscription.read",
    write_permission="subscriptions.subscription.write",
    natural_key="name",
    filters=("status", "company_id", "sort"),
    columns=(
        ImpexColumn("name", required=True),
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            required=True,
            getter=lambda s: getattr(s, "company_name", None),
        ),
        ImpexColumn(
            "status",
            data_type="select",
            clearable=False,
            options=tuple(status.value for status in SubscriptionStatus),
        ),
        ImpexColumn(
            "interval",
            data_type="select",
            clearable=False,
            options=tuple(interval.value for interval in SubscriptionInterval),
        ),
        # The tenant-defined category (#142), referenced by its org-unique key.
        ImpexColumn(
            "type",
            data_type="fk",
            field="subscription_type_id",
            getter=lambda s: getattr(s, "subscription_type_key", None),
        ),
        ImpexColumn("start_date", data_type="date", required=True, clearable=False),
        ImpexColumn("end_date", data_type="date"),
        ImpexColumn("next_invoice_date", data_type="date"),
        ImpexColumn("included_hours", data_type="number"),
        # The price valid today; a changed value appends to the price history on update.
        ImpexColumn("amount", data_type="number", clearable=False),
        ImpexColumn("notes"),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "type": _resolve_type,
    },
)
