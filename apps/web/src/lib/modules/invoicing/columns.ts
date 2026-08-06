import type { ColumnMeta } from "$lib/core/table/columns";

export const INVOICES_TABLE_ID = "invoices";
export const QUOTES_TABLE_ID = "quotes";
export const UNINVOICED_TABLE_ID = "uninvoiced";
export const BACKLOG_TABLE_ID = "invoicing-backlog";

/**
 * The org-wide "still to invoice" report (#277). No `sortKey`s: rows arrive bucketed under
 * the chosen grouping, ordered by date inside each section — a header sort would reorder
 * what the grouping laid out (docs/UX.md #38). The page hides the column that matches the
 * active grouping; the section headers already say it on every row.
 */
export const UNINVOICED_COLUMNS: ColumnMeta[] = [
  { key: "date", labelKey: "invoicing.uninvoiced.field.date", primary: true, width: 110 },
  { key: "company", labelKey: "invoicing.uninvoiced.field.company", defaultVisible: true },
  { key: "project", labelKey: "invoicing.uninvoiced.field.project", defaultVisible: true },
  { key: "user", labelKey: "invoicing.uninvoiced.field.employee", defaultVisible: true },
  {
    key: "description",
    labelKey: "invoicing.uninvoiced.field.description",
    defaultVisible: true,
  },
  {
    key: "hours",
    labelKey: "invoicing.uninvoiced.field.hours",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "amount",
    labelKey: "invoicing.uninvoiced.field.amount",
    align: "right",
    defaultVisible: true,
  },
];

/**
 * The recurring half of that report (#302): agreement periods and domain renewals no document
 * claims yet. Same no-`sortKey` rule and the same hide-the-grouped-column rule as above — one
 * table id for both sources, because a user who widened "Klant" while looking at domains means
 * it just as much when they switch to subscriptions.
 */
export const BACKLOG_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "invoicing.backlog.field.name", primary: true },
  { key: "company", labelKey: "invoicing.backlog.field.company", defaultVisible: true },
  {
    key: "period",
    labelKey: "invoicing.backlog.field.period",
    align: "right",
    defaultVisible: true,
    width: 190,
  },
  {
    key: "automation",
    labelKey: "invoicing.backlog.field.automation",
    defaultVisible: true,
    width: 150,
  },
  {
    key: "amount",
    labelKey: "invoicing.backlog.field.amount",
    align: "right",
    defaultVisible: true,
  },
];

// Every column but one states a `width`, because the table lays out `table-fixed`: an undeclared
// width there is not "as wide as it needs", it is an equal share of the slack — which put a
// client's name in a 150px column and wrapped every row to five lines. The client is the widest
// thing on the row and takes the remainder (`flex`), *not* the primary column: an invoice is
// identified by a number, and a number needs 130px whatever the screen.
export const INVOICE_COLUMNS: ColumnMeta[] = [
  {
    key: "number",
    labelKey: "invoicing.field.number",
    sortKey: "number",
    primary: true,
    width: 130,
  },
  { key: "company", labelKey: "invoicing.field.company", defaultVisible: true, flex: true },
  {
    key: "issue_date",
    labelKey: "invoicing.field.issue_date",
    sortKey: "issue_date",
    align: "right",
    defaultVisible: true,
    width: 130,
  },
  {
    key: "due_date",
    labelKey: "invoicing.field.due_date",
    sortKey: "due_date",
    align: "right",
    defaultVisible: true,
    width: 130,
  },
  {
    key: "status",
    labelKey: "invoicing.field.status",
    sortKey: "status",
    defaultVisible: true,
    width: 120,
  },
  {
    key: "total",
    labelKey: "invoicing.field.total",
    sortKey: "total",
    align: "right",
    defaultVisible: true,
    width: 110,
  },
  {
    key: "outstanding",
    labelKey: "invoicing.field.outstanding",
    align: "right",
    defaultVisible: true,
    width: 120,
  },
  { key: "reference", labelKey: "invoicing.field.reference", defaultVisible: false, width: 160 },
  { key: "reminders", labelKey: "invoicing.field.reminders", defaultVisible: false, width: 110 },
];

export const QUOTE_COLUMNS: ColumnMeta[] = [
  {
    key: "number",
    labelKey: "invoicing.field.number",
    sortKey: "number",
    primary: true,
    width: 130,
  },
  { key: "company", labelKey: "invoicing.field.company", defaultVisible: true },
  {
    key: "issue_date",
    labelKey: "invoicing.field.quote_date",
    sortKey: "issue_date",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "valid_until",
    labelKey: "invoicing.field.valid_until",
    sortKey: "valid_until",
    align: "right",
    defaultVisible: true,
  },
  { key: "status", labelKey: "invoicing.field.status", sortKey: "status", defaultVisible: true },
  {
    key: "total",
    labelKey: "invoicing.field.total",
    sortKey: "total",
    align: "right",
    defaultVisible: true,
  },
  { key: "reference", labelKey: "invoicing.field.reference", defaultVisible: false },
];
