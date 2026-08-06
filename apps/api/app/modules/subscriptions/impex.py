"""CSV import/export shape for subscriptions and their two catalogs (issue #77, §17).

Three descriptors:

* ``subscription`` — the agreements. Upsert matches on ``name``. ``amount`` is the price valid
  today: on create it seeds the price history, on update the service appends a new price row
  when it changed — exactly the form's behaviour, because the import goes through the same
  service.
* ``subscription_type`` — the tenant's own categories (#142), keyed on their org-unique ``key``
  with one label column per shipped locale.
* ``subscription_template`` — the presets. Editing a preset never reprices an agreement made
  from it (that is the whole point of the preset model), so a bulk edit here is safe by
  construction.

What is deliberately **not** here: an agreement's invoice ``lines`` and its project/task
``links``, and a preset's default ``lines``. They are lists of records inside one row, and a
flat cell has no honest shape for them — a "lines" column would either lose data on export or
silently replace the whole list on import. They stay a form concern.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.impex.spec import locale_label_columns, merge_locale_labels
from app.core.tenancy import RequestContext
from app.modules.subscriptions.models import (
    Subscription,
    SubscriptionInterval,
    SubscriptionStatus,
    SubscriptionTemplate,
    SubscriptionType,
)
from app.modules.subscriptions.schemas import (
    RolloverRule,
    SubscriptionCreate,
    SubscriptionTemplateCreate,
    SubscriptionTemplateUpdate,
    SubscriptionTypeCreate,
    SubscriptionTypeUpdate,
    SubscriptionUpdate,
)
from app.modules.subscriptions.service import (
    SubscriptionService,
    SubscriptionTemplateService,
    SubscriptionTypeService,
)

_FIELDS = (
    "name", "end_date", "next_invoice_date", "included_hours", "notes", "company_id",
    "subscription_type_id", "subscription_template_id", "currency", "interval_count",
    "notice_period_days",
)

#: The two halves of ``RolloverRule`` as two cells — a nested object has no flat spelling, and
#: these are the only two fields it has.
_ROLLOVER_MODES = ("none", "carry")


def _rollover(values: dict[str, Any], current: Any = None) -> RolloverRule | None:
    """Assemble the rule from whichever of its two columns the file carried.

    Absent columns leave the stored rule alone (``None``); a carried one is merged over it, so
    a file that only sets ``rollover_mode`` does not silently drop an expiry the tenant
    configured in the form.
    """
    if "rollover_mode" not in values and "rollover_expires_after_periods" not in values:
        return None
    base = current if isinstance(current, dict) else {}
    mode = values.get("rollover_mode") or base.get("mode") or "none"
    if "rollover_expires_after_periods" in values:
        raw = values["rollover_expires_after_periods"]
        periods = int(Decimal(raw)) if raw else None
    else:
        periods = base.get("expires_after_periods")
    # "carry" is the only mode an expiry means anything under; the schema forbids the pairing
    # nowhere, but storing a period against "none" is noise a later read has to ignore.
    return RolloverRule(
        mode=mode, expires_after_periods=periods if mode == "carry" else None
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
    # The preset an agreement follows, by name — the same grouped-query shape as the type.
    template_ids = {
        s.subscription_template_id for s in items if s.subscription_template_id is not None
    }
    if template_ids:
        rows = await ctx.session.execute(
            ctx.repo(SubscriptionTemplate)
            .scoped_select()
            .where(SubscriptionTemplate.id.in_(template_ids))
            .with_only_columns(SubscriptionTemplate.id, SubscriptionTemplate.name)
        )
        names = dict(rows.all())
        for sub in items:
            sub.subscription_template_name = names.get(sub.subscription_template_id)  # type: ignore[attr-defined]
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


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    stmt = ctx.repo(Subscription).scoped_select().where(Subscription.name.in_(values))
    found: dict[str, list[Any]] = {}
    for sub in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(sub.name, []).append(sub)
    return found


def _optional_int(values: dict[str, Any], key: str) -> Any:
    """A number cell the schema wants as an ``int`` — the engine hands them over as decimal
    strings, and an emptied cell is a real ``None`` (the field is nullable)."""
    raw = values.get(key)
    return int(Decimal(raw)) if raw else None


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    rollover = _rollover(values)
    return await SubscriptionService(ctx).create(
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
            interval_count=int(Decimal(values["interval_count"]))
            if values.get("interval_count")
            else 1,
            currency=values.get("currency") or "EUR",
            subscription_type_id=values.get("subscription_type_id"),
            subscription_template_id=values.get("subscription_template_id"),
            start_date=values["start_date"],
            end_date=values.get("end_date"),
            next_invoice_date=values.get("next_invoice_date"),
            included_hours=values.get("included_hours"),
            notice_period_days=_optional_int(values, "notice_period_days"),
            **({"rollover": rollover} if rollover is not None else {}),
            notes=values.get("notes"),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, sub: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _FIELDS if key in values}
    if "interval_count" in fields:
        fields["interval_count"] = _optional_int(values, "interval_count")
    if "notice_period_days" in fields:
        fields["notice_period_days"] = _optional_int(values, "notice_period_days")
    if values.get("status"):
        fields["status"] = SubscriptionStatus(values["status"])
    if values.get("interval"):
        fields["interval"] = SubscriptionInterval(values["interval"])
    if values.get("start_date"):
        fields["start_date"] = values["start_date"]
    if values.get("amount"):
        fields["amount"] = Decimal(values["amount"])
    rollover = _rollover(values, sub.rollover)
    if rollover is not None:
        fields["rollover"] = rollover
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await SubscriptionService(ctx).update(sub.id, SubscriptionUpdate(**fields))


SUBSCRIPTION_IMPEX = ImpexDescriptor(
    entity_type="subscription",
    read_permission="subscriptions.subscription.read",
    write_permission="subscriptions.subscription.write",
    natural_keys=("name",),
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
            option_label_key="subscriptions.status.{option}",
        ),
        ImpexColumn(
            "interval",
            data_type="select",
            clearable=False,
            options=tuple(interval.value for interval in SubscriptionInterval),
            option_label_key="subscriptions.interval.{option}",
        ),
        # The tenant-defined category (#142), referenced by its org-unique key.
        ImpexColumn(
            "type",
            data_type="fk",
            field="subscription_type_id",
            getter=lambda s: getattr(s, "subscription_type_key", None),
        ),
        # "every 2 months" is interval + count; without the count the two are one word apart
        # and a bimonthly agreement re-imports as a monthly one.
        ImpexColumn("interval_count", data_type="number", clearable=False),
        # The preset this agreement was made from — provenance, and what makes a later rename
        # of the preset reach it (#142).
        ImpexColumn(
            "template",
            data_type="fk",
            field="subscription_template_id",
            getter=lambda s: getattr(s, "subscription_template_name", None),
            aliases=("standaardabonnement", "preset", "sjabloon"),
        ),
        ImpexColumn("start_date", data_type="date", required=True, clearable=False),
        ImpexColumn("end_date", data_type="date"),
        ImpexColumn("next_invoice_date", data_type="date"),
        ImpexColumn("included_hours", data_type="number"),
        # The price valid today; a changed value appends to the price history on update.
        ImpexColumn("amount", data_type="number", clearable=False),
        # NOT NULL with a default, so an empty cell means "not carried by this file".
        ImpexColumn("currency", clearable=False, aliases=("valuta",)),
        ImpexColumn(
            "rollover_mode",
            data_type="select",
            clearable=False,
            options=_ROLLOVER_MODES,
            getter=lambda s: (s.rollover or {}).get("mode") or "none",
        ),
        ImpexColumn(
            "rollover_expires_after_periods",
            data_type="number",
            getter=lambda s: (s.rollover or {}).get("expires_after_periods"),
        ),
        ImpexColumn("notice_period_days", data_type="number", aliases=("opzegtermijn",)),
        ImpexColumn("notes"),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "type": _resolve_type,
        "template": name_or_id_resolver("subscription_templates"),
    },
)


# --- the two tenant catalogs (#142) ---------------------------------------------------- #
async def _fetch_types(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    """The catalog is a handful of rows read in one query, so it pages in memory.

    ``include_inactive`` is on: an export that silently dropped the deactivated types would
    re-import as a request to delete them, which is not what "export, edit, import" means.
    """
    items = await SubscriptionTypeService(ctx).list(include_inactive=True)
    return items[offset : offset + limit]


async def _find_type(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    stmt = ctx.repo(SubscriptionType).scoped_select().where(SubscriptionType.key.in_(values))
    found: dict[str, list[Any]] = {}
    for row in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(row.key, []).append(row)
    return found


async def _create_type(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await SubscriptionTypeService(ctx).create(
        SubscriptionTypeCreate(
            key=values["key"],
            label_i18n=merge_locale_labels(values) or {},
            position=_optional_int(values, "position") or 0,
            active=values.get("active") is not False,
        )
    )


async def _update_type(ctx: RequestContext, sub_type: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {}
    labels = merge_locale_labels(values, sub_type.label_i18n)
    if labels is not None:
        fields["label_i18n"] = labels
    if "position" in values:
        fields["position"] = _optional_int(values, "position") or 0
    if "active" in values and values["active"] is not None:
        fields["active"] = values["active"]
    if fields:
        # ``key`` is immutable by omission — which is also why it is the natural key.
        await SubscriptionTypeService(ctx).update(sub_type.id, SubscriptionTypeUpdate(**fields))


SUBSCRIPTION_TYPE_IMPEX = ImpexDescriptor(
    entity_type="subscription_type",
    # Reading the catalog rides the subscription read grant (see permissions.py); managing it
    # is the admin-only catalog permission, so bulk-editing it needs exactly what the settings
    # screen needs.
    read_permission="subscriptions.subscription.read",
    write_permission="subscriptions.type.manage",
    natural_keys=("key",),
    filters=(),
    columns=(
        ImpexColumn(
            "key", required=True, clearable=False, aliases=("sleutel", "code", "type")
        ),
        *locale_label_columns(aliases={"nl": ("label", "naam"), "en": ("label", "name")}),
        ImpexColumn("position", data_type="number", clearable=False, aliases=("volgorde",)),
        ImpexColumn("active", data_type="bool", clearable=False, aliases=("actief",)),
        # ``task_template_ids`` is a list of ids into the tasks module — a list has no honest
        # single-cell spelling, and these are configured where the templates are.
    ),
    fetch_page=_fetch_types,
    find_existing=_find_type,
    create_row=_create_type,
    update_row=_update_type,
)


async def _fetch_templates(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items = await SubscriptionTemplateService(ctx).list()
    page = items[offset : offset + limit]
    type_ids = {t.subscription_type_id for t in page if t.subscription_type_id is not None}
    if type_ids:
        rows = await ctx.session.execute(
            ctx.repo(SubscriptionType)
            .scoped_select()
            .where(SubscriptionType.id.in_(type_ids))
            .with_only_columns(SubscriptionType.id, SubscriptionType.key)
        )
        keys = dict(rows.all())
        for template in page:
            template.subscription_type_key = keys.get(template.subscription_type_id)  # type: ignore[attr-defined]
    return page


async def _find_template(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    stmt = (
        ctx.repo(SubscriptionTemplate)
        .scoped_select()
        .where(SubscriptionTemplate.name.in_(values))
    )
    found: dict[str, list[Any]] = {}
    for row in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(row.name, []).append(row)
    return found


_TEMPLATE_FIELDS = ("name", "subscription_type_id", "currency", "notes")


def _template_fields(values: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {key: values[key] for key in _TEMPLATE_FIELDS if key in values}
    for key in ("interval_count", "notice_period_days", "position"):
        if key in values:
            fields[key] = _optional_int(values, key)
    for key in ("amount", "included_hours"):
        if key in values:
            fields[key] = Decimal(values[key]) if values[key] else None
    if values.get("interval"):
        fields["interval"] = SubscriptionInterval(values["interval"])
    return fields


async def _create_template(ctx: RequestContext, values: dict[str, Any]) -> Any:
    fields = _template_fields(values)
    rollover = _rollover(values)
    return await SubscriptionTemplateService(ctx).create(
        SubscriptionTemplateCreate(
            **{
                **fields,
                "name": values["name"],
                "currency": fields.get("currency") or "EUR",
                "interval_count": fields.get("interval_count") or 1,
                "position": fields.get("position") or 0,
            },
            **({"rollover": rollover} if rollover is not None else {}),
        )
    )


async def _update_template(ctx: RequestContext, template: Any, values: dict[str, Any]) -> None:
    fields = _template_fields(values)
    rollover = _rollover(values, template.rollover)
    if rollover is not None:
        fields["rollover"] = rollover
    if fields:
        await SubscriptionTemplateService(ctx).update(
            template.id, SubscriptionTemplateUpdate(**fields)
        )


SUBSCRIPTION_TEMPLATE_IMPEX = ImpexDescriptor(
    entity_type="subscription_template",
    read_permission="subscriptions.subscription.read",
    write_permission="subscriptions.template.manage",
    # ``name`` is not unique per org, so two presets sharing one become an ambiguous match
    # rather than a coin flip — the same answer projects and hosting give.
    natural_keys=("name",),
    filters=(),
    columns=(
        ImpexColumn("name", required=True, clearable=False, aliases=("naam",)),
        ImpexColumn(
            "type",
            data_type="fk",
            field="subscription_type_id",
            getter=lambda t: getattr(t, "subscription_type_key", None),
        ),
        ImpexColumn(
            "interval",
            data_type="select",
            clearable=False,
            options=tuple(interval.value for interval in SubscriptionInterval),
            option_label_key="subscriptions.interval.{option}",
        ),
        ImpexColumn("interval_count", data_type="number", clearable=False),
        ImpexColumn("amount", data_type="number", aliases=("prijs", "bedrag", "price")),
        ImpexColumn("currency", clearable=False, aliases=("valuta",)),
        ImpexColumn("included_hours", data_type="number", aliases=("inbegrepen uren",)),
        ImpexColumn(
            "rollover_mode",
            data_type="select",
            clearable=False,
            options=_ROLLOVER_MODES,
            getter=lambda t: (t.rollover or {}).get("mode") or "none",
        ),
        ImpexColumn(
            "rollover_expires_after_periods",
            data_type="number",
            getter=lambda t: (t.rollover or {}).get("expires_after_periods"),
        ),
        ImpexColumn("notice_period_days", data_type="number", aliases=("opzegtermijn",)),
        ImpexColumn("notes", aliases=("notities", "opmerkingen")),
        ImpexColumn("position", data_type="number", clearable=False, aliases=("volgorde",)),
    ),
    fk_resolvers={"type": _resolve_type},
    fetch_page=_fetch_templates,
    find_existing=_find_template,
    create_row=_create_template,
    update_row=_update_template,
)
