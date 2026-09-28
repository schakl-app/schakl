/**
 * The meetings register's columns (docs/UX.md: every list is the shared `DataTable`, driven by
 * column descriptors) and the keys its URL is narrowed by.
 *
 * Every column but the title states a `width`, because the table lays out `table-fixed`: an
 * undeclared width is an equal share of the slack, not "as wide as it needs". The title is the
 * identity and the widest thing on the row, so it is the one that absorbs the rest.
 *
 * A `sortKey` is declared exactly where `GET /meetings?sort=` can order by the column
 * (`MEETING_SORTABLE`) — the client is named through the directory seam, not a column the
 * query can order on, so its header is not clickable.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const MEETINGS_TABLE_ID = "meetings";

export const MEETING_COLUMNS: ColumnMeta[] = [
  { key: "title", labelKey: "meetings.list.title", sortKey: "title", primary: true },
  { key: "company", labelKey: "meetings.list.client", defaultVisible: true, width: 200 },
  {
    key: "occurred_at",
    labelKey: "meetings.list.when",
    sortKey: "occurred_at",
    defaultVisible: true,
    width: 170,
  },
  {
    key: "duration",
    labelKey: "meetings.list.duration",
    sortKey: "duration",
    align: "right",
    defaultVisible: true,
    width: 110,
  },
  // Wide enough for the longest label this vocabulary holds — "Verslag wordt geschreven" —
  // rather than for the one on the screen it was measured on (#347).
  {
    key: "status",
    labelKey: "meetings.list.status",
    sortKey: "status",
    defaultVisible: true,
    width: 220,
  },
  {
    key: "action_items",
    labelKey: "meetings.list.action_items",
    align: "right",
    defaultVisible: true,
    width: 120,
  },
  {
    key: "decisions",
    labelKey: "meetings.list.decisions",
    align: "right",
    defaultVisible: false,
    width: 120,
  },
  {
    key: "kind",
    labelKey: "meetings.list.kind",
    sortKey: "kind",
    defaultVisible: false,
    width: 130,
  },
  { key: "project", labelKey: "meetings.list.project", defaultVisible: false, width: 200 },
  {
    key: "owner",
    labelKey: "meetings.list.owner",
    sortKey: "owner",
    defaultVisible: false,
    width: 180,
  },
];

/** What the register can be narrowed by: the keys the URL carries and the bar renders. */
export const MEETING_FILTERS = ["q", "company", "status"] as const;

export type MeetingFilterKey = (typeof MEETING_FILTERS)[number];

/**
 * The states a worker holds a row in. The API refuses to delete one of these
 * (`meetings.error.busy`), so the bulk bar counts them out before anyone presses (#299).
 */
export function workerHolds(status: string): boolean {
  return status === "transcribing" || status === "summarising";
}
