/**
 * The columns the website list can show (#251, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/websites/service.py::SORTABLE`): a website has no name of its own,
 * so `name` orders by the parent domain's name, `company` walks domain → company, `hosting`
 * by the account's name. The technical owner is a party (agency/company/employee/contact),
 * which the API cannot order by one rule — its header stays honest and quiet.
 *
 * Every column but the primary one carries a `width`, because the table lays out fixed: a
 * declared width is the used width there, and exactly one column may leave it to absorb the
 * slack. Here that is the domain name, which is the longest cell on the row anyway.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const WEBSITES_TABLE_ID = "websites";

export const WEBSITE_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "websites.title", sortKey: "name", primary: true, width: 260 },
  {
    key: "company",
    labelKey: "websites.company",
    sortKey: "company",
    defaultVisible: true,
    width: 220,
  },
  // Hosting, who looks after it, whether it is up and when we filed it are the agency's view
  // of a site; a client's websites list is just the list (`audience`, core/table/columns.ts).
  {
    key: "hosting",
    labelKey: "websites.hosting",
    sortKey: "hosting",
    defaultVisible: true,
    width: 220,
    audience: "staff",
  },
  { key: "technical_owner", labelKey: "websites.technical_owner", width: 200, audience: "staff" },
  // A pill or a dash, so it needs room for the header and nothing more.
  {
    key: "uptime",
    labelKey: "websites.uptime_short",
    sortKey: "uptime",
    defaultVisible: true,
    width: 110,
    audience: "staff",
  },
  {
    key: "created_at",
    labelKey: "table.column.created_at",
    sortKey: "created_at",
    align: "right",
    width: 130,
    audience: "staff",
  },
];
