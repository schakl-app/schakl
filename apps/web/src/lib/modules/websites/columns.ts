/**
 * The columns the website list can show (#251, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/websites/service.py::SORTABLE`): a website has no name of its own,
 * so `name` orders by the parent domain's name, `company` walks domain → company, `hosting`
 * by the account's name. The technical owner is a party (agency/company/employee/contact),
 * which the API cannot order by one rule — its header stays honest and quiet.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const WEBSITES_TABLE_ID = "websites";

export const WEBSITE_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "websites.title", sortKey: "name", primary: true, width: 260 },
  { key: "company", labelKey: "websites.company", sortKey: "company", defaultVisible: true },
  { key: "hosting", labelKey: "websites.hosting", sortKey: "hosting", defaultVisible: true },
  { key: "technical_owner", labelKey: "websites.technical_owner" },
  { key: "uptime", labelKey: "websites.uptime_short", sortKey: "uptime", defaultVisible: true },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
