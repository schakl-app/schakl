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
import { columnsForViewer, type ColumnMeta } from "$lib/core/table/columns";

export const COMPANIES_TABLE_ID = "companies";

/** The derived budget column; its presence is what makes the list pay for the aggregate. */
export const HOURS_COLUMN = "hours";

// Every non-primary column states a `width`, because the table lays out `table-fixed`: an
// undeclared width there is not "as wide as it needs", it is an equal share of the slack the
// primary column is meant to absorb — so the client name ended up exactly as wide as its
// website. The five shown by default sum to 760px beside the two gutters; the name takes the
// rest, which is what makes it the widest column on a real screen instead of one of five equals.
/**
 * The built-in columns this viewer may choose from. Four are the agency's and stay off a
 * client-portal login's table (`audience: "staff"` below, the rule every list shares in
 * `core/table/columns.ts`): who at the agency handles the account, when the client was entered,
 * the budget burn (#449: the API blanks `hours` for a client, so the column would only ever draw
 * dashes) — and the phone number, which is the client's own and not what they opened this screen
 * to read. Filtered out of the *list* the page and its server load both build from, not hidden
 * per cell, so the column picker cannot offer them either and the load never asks the API to
 * compute the burn.
 */
export function companyColumns(isPortal: boolean): ColumnMeta[] {
  return columnsForViewer(COMPANY_COLUMNS, { isPortal });
}

export const COMPANY_COLUMNS: ColumnMeta[] = [
  // Klantnummer first: on a list that carries one it is how people refer to the client, and
  // it is short. Shown by default because numbering is on by default — an org that turns it
  // off in Instellingen → Bedrijven hides the column with the personal column picker.
  {
    key: "client_number",
    labelKey: "companies.client_number",
    sortKey: "client_number",
    defaultVisible: true,
    width: 120,
  },
  { key: "name", labelKey: "companies.name", sortKey: "name", primary: true, width: 260 },
  // Opt-in, and not sortable: the API orders clients by their label, which is what the list is
  // read as. It earns a column because there is one job — reconciling a bank statement, a
  // bookkeeper's list, a KvK export — where the *other* name is the only one you have, and for
  // that job a column you can switch on and export beats searching one client at a time.
  { key: "legal_name", labelKey: "companies.legal_name", width: 220 },
  { key: "website", labelKey: "companies.website", defaultVisible: true, width: 180 },
  // 150, not 120 (#347). A status is a short closed vocabulary and is exactly the value that
  // must never be cut, and 120 left an 88 px content box for a chip needing 89: `Onboardi…`,
  // one pixel short. The width is measured against the longest label the column can hold, in
  // both locales, rather than against the one that happened to be on screen.
  {
    key: "status",
    labelKey: "companies.field.status",
    sortKey: "status",
    defaultVisible: true,
    width: 150,
  },
  // Sorts by the *primary* assignee's display name; the API orders by it, not by user id.
  {
    key: "assignees",
    labelKey: "companies.field.assignees",
    sortKey: "assignee",
    defaultVisible: true,
    width: 140,
    audience: "staff",
  },
  // Shown by default — seeing who has budget left is the point of the column (#25). Turning it
  // off is what proves a hidden aggregate costs nothing. The header names what the cell prints
  // (spent of budget, #340); what remains is on hover, in words.
  {
    key: HOURS_COLUMN,
    labelKey: "table.column.hours_burn",
    align: "right",
    defaultVisible: true,
    width: 200,
    audience: "staff",
  },
  // Opt-in like invoice_email: a hidden column costs nothing, and most lists lead with name.
  { key: "phone", labelKey: "companies.phone", width: 150, audience: "staff" },
  { key: "invoice_email", labelKey: "companies.invoice_email", width: 200 },
  {
    key: "created_at",
    labelKey: "table.column.created_at",
    sortKey: "created_at",
    align: "right",
    width: 120,
    audience: "staff",
  },
];
