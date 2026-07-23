/**
 * The columns the subscriptions list can show (#153, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/subscriptions/service.py::SORTABLE`), which covers every column:
 * company sorts by the name the cell prints, type by the tenant's declared position, interval by
 * period length, amount by the current price — all server-side, because the list is paginated.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const SUBSCRIPTIONS_TABLE_ID = "subscriptions";

export const SUBSCRIPTION_COLUMNS: ColumnMeta[] = [
  {
    key: "name",
    labelKey: "subscriptions.field.name",
    sortKey: "name",
    primary: true,
    width: 220,
  },
  {
    key: "company",
    labelKey: "subscriptions.field.company",
    sortKey: "company",
    defaultVisible: true,
  },
  { key: "type", labelKey: "subscriptions.field.type", sortKey: "type", defaultVisible: true },
  {
    key: "interval",
    labelKey: "subscriptions.field.interval",
    sortKey: "interval",
    defaultVisible: true,
  },
  {
    key: "amount",
    labelKey: "subscriptions.field.amount",
    sortKey: "amount",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "next_invoice",
    labelKey: "subscriptions.field.next_invoice",
    sortKey: "next_invoice_date",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "status",
    labelKey: "subscriptions.field.status",
    sortKey: "status",
    defaultVisible: true,
  },
  { key: "start_date", labelKey: "subscriptions.field.start_date", sortKey: "start_date" },
  {
    key: "included_hours",
    labelKey: "subscriptions.field.included_hours",
    sortKey: "included_hours",
    align: "right",
  },
];

/**
 * The catalog tables (#229). Unlike the list above, their `sortKey`s are honoured by the
 * page's own `load` — the catalog is small and fetched whole, so the server-side sort the
 * DataTable contract expects happens there, not in the API.
 */
export const SUBSCRIPTION_TEMPLATES_TABLE_ID = "subscription_templates";

export const SUBSCRIPTION_TEMPLATE_COLUMNS: ColumnMeta[] = [
  {
    key: "name",
    labelKey: "subscriptions.field.name",
    sortKey: "name",
    primary: true,
    width: 220,
  },
  { key: "type", labelKey: "subscriptions.field.type", sortKey: "type", defaultVisible: true },
  {
    key: "interval",
    labelKey: "subscriptions.field.interval",
    sortKey: "interval",
    defaultVisible: true,
  },
  {
    key: "amount",
    labelKey: "subscriptions.field.amount",
    sortKey: "amount",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "included_hours",
    labelKey: "subscriptions.field.included_hours",
    sortKey: "included_hours",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "notice_period_days",
    labelKey: "subscriptions.field.notice_period_days",
    sortKey: "notice_period_days",
    align: "right",
  },
  { key: "notes", labelKey: "subscriptions.field.notes" },
];

export const SUBSCRIPTION_TYPES_TABLE_ID = "subscription_types";

export const SUBSCRIPTION_TYPE_COLUMNS: ColumnMeta[] = [
  { key: "label", labelKey: "common.label_field", sortKey: "label", primary: true, width: 220 },
  { key: "key", labelKey: "settings.subscriptions.key", sortKey: "key", defaultVisible: true },
  {
    key: "tasks",
    labelKey: "settings.subscriptions.task_templates",
    sortKey: "tasks",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "active",
    labelKey: "subscriptions.field.status",
    sortKey: "active",
    defaultVisible: true,
  },
];
