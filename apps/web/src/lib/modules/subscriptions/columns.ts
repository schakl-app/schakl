/**
 * The columns the subscriptions list can show (#153, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/subscriptions/service.py::SORTABLE`); a column without one has a
 * quiet header because the server genuinely cannot order by it.
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
  { key: "company", labelKey: "subscriptions.field.company", defaultVisible: true },
  { key: "type", labelKey: "subscriptions.field.type", defaultVisible: true },
  {
    key: "amount",
    labelKey: "subscriptions.field.amount",
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
  { key: "status", labelKey: "subscriptions.field.status", sortKey: "status", defaultVisible: true },
  { key: "start_date", labelKey: "subscriptions.field.start_date", sortKey: "start_date" },
  {
    key: "included_hours",
    labelKey: "subscriptions.field.included_hours",
    align: "right",
  },
];
