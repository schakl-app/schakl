/**
 * The columns a client list can show (#24, #25).
 *
 * Plain metadata, no Svelte: the page's server `load` reads this to decide what to ask the API
 * for — notably whether the expensive `hours` roll-up is visible at all — and the component reads
 * the same list to render headers and cells.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/companies/service.py::SORTABLE`).
 * A column with no `sortKey` has a quiet header, because the server genuinely cannot order by it.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const COMPANIES_TABLE_ID = "companies";

/** The derived budget column; its presence is what makes the list pay for the aggregate. */
export const HOURS_COLUMN = "hours";

export const COMPANY_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "companies.name", sortKey: "name", primary: true, width: 260 },
  { key: "website", labelKey: "companies.website", defaultVisible: true },
  { key: "status", labelKey: "companies.field.status", sortKey: "status", defaultVisible: true },
  { key: "assignees", labelKey: "companies.field.assignees", defaultVisible: true },
  { key: HOURS_COLUMN, labelKey: "table.column.available_hours", align: "right", width: 200 },
  { key: "invoice_email", labelKey: "companies.invoice_email" },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
