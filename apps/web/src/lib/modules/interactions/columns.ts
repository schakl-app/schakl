/**
 * The columns the Interacties list can show (#168, #24).
 *
 * Every column carries the API's `?sort=` key (#238) — the shared `DataTable` contract, like
 * every other list. Newest-first stays the default; the day sections only render while the
 * order *is* the timeline, so sections and sort can never disagree. The `linked` column sorts
 * as `contact`: of the records a row hangs on, the contact is who the moment was *with*.
 *
 * The four fixed widths below sum to 640px, and under the table's fixed layout that sum is what
 * the subject column does *not* get: it is the flexible one, so it lives on whatever the grid
 * has left after these, the checkbox gutter and the actions cell. They were 750px, which left a
 * subject — the one column carrying the email's own text and its snippet — barely a hundred
 * pixels on a laptop. Widen one of these only by taking it off the others.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const INTERACTIONS_TABLE_ID = "interactions";

export const INTERACTION_COLUMNS: ColumnMeta[] = [
  {
    key: "subject",
    labelKey: "interactions.column.subject",
    primary: true,
    width: 380,
    sortKey: "subject",
  },
  {
    key: "kind",
    labelKey: "interactions.column.kind",
    defaultVisible: true,
    width: 130,
    sortKey: "kind",
  },
  {
    key: "linked",
    labelKey: "interactions.column.linked",
    defaultVisible: true,
    width: 220,
    sortKey: "contact",
  },
  {
    key: "owner",
    labelKey: "interactions.column.owner",
    defaultVisible: true,
    width: 150,
    sortKey: "owner",
  },
  {
    key: "when",
    labelKey: "interactions.column.when",
    defaultVisible: true,
    width: 140,
    sortKey: "occurred_at",
  },
];
