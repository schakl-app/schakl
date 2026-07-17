import type { ColumnMeta } from "$lib/core/table/columns";

export const INVOICES_TABLE_ID = "invoices";
export const QUOTES_TABLE_ID = "quotes";

export const INVOICE_COLUMNS: ColumnMeta[] = [
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
    labelKey: "invoicing.field.issue_date",
    sortKey: "issue_date",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "due_date",
    labelKey: "invoicing.field.due_date",
    sortKey: "due_date",
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
  {
    key: "outstanding",
    labelKey: "invoicing.field.outstanding",
    align: "right",
    defaultVisible: true,
  },
  { key: "reference", labelKey: "invoicing.field.reference", defaultVisible: false },
  { key: "reminders", labelKey: "invoicing.field.reminders", defaultVisible: false },
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
