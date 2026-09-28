/**
 * The report register's columns (docs/UX.md: every list is the shared `DataTable`) and the keys
 * its URL is narrowed by.
 *
 * **Which columns are the desk's and which are the reader's** is stated here rather than in the
 * markup (#373): "Klantrapportage" names the other kind of document, which a client has never
 * seen, and the status is a state in our workflow — true, none of their business, and alarming.
 * `audience: "staff"` keeps them off a client's table *and* out of its column picker.
 *
 * A `sortKey` is declared exactly where `GET /reporting/reports?sort=` can order by the column
 * (`REPORT_SORTABLE`).
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const REPORTS_TABLE_ID = "reports";

export const REPORT_COLUMNS: ColumnMeta[] = [
  { key: "company", labelKey: "reporting.list.client", sortKey: "company", primary: true },
  {
    key: "period",
    labelKey: "reporting.list.period",
    sortKey: "period",
    defaultVisible: true,
    // The fallback label is two whole dates ("1 augustus 2026 – 28 augustus 2026").
    width: 270,
  },
  {
    key: "audience",
    labelKey: "reporting.list.audience",
    sortKey: "audience",
    defaultVisible: true,
    width: 170,
    audience: "staff",
  },
  // Wide enough for "Klaar om na te kijken" plus the spinner a generating row carries.
  {
    key: "status",
    labelKey: "reporting.list.status",
    sortKey: "status",
    defaultVisible: true,
    width: 210,
    audience: "staff",
  },
  {
    key: "sent",
    labelKey: "reporting.list.sent",
    sortKey: "sent_at",
    defaultVisible: true,
    width: 200,
  },
  { key: "title", labelKey: "reporting.list.title", defaultVisible: false, width: 220 },
  {
    key: "generated_by",
    labelKey: "reporting.list.generated_by",
    defaultVisible: false,
    width: 180,
    audience: "staff",
  },
  {
    key: "created_at",
    labelKey: "reporting.list.created",
    sortKey: "created_at",
    defaultVisible: false,
    width: 170,
    audience: "staff",
  },
];

/** What the register can be narrowed by: the keys the URL carries and the bar renders. */
export const REPORT_FILTERS = ["company", "audience", "status"] as const;

export type ReportFilterKey = (typeof REPORT_FILTERS)[number];

/** The statuses worth a chip. `generating` passes by itself and is never something to look for. */
export const REPORT_FILTER_STATUSES = ["draft", "ready", "sent", "failed"] as const;
