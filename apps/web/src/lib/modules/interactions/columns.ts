/**
 * The columns the Interacties list can show (#168, #24).
 *
 * Every column carries the API's `?sort=` key (#238) — the shared `DataTable` contract, like
 * every other list. Newest-first stays the default; the day sections only render while the
 * order *is* the timeline, so sections and sort can never disagree. The `linked` column sorts
 * as `contact`: of the records a row hangs on, the contact is who the moment was *with*.
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
    width: 170,
    sortKey: "kind",
  },
  {
    key: "linked",
    labelKey: "interactions.column.linked",
    defaultVisible: true,
    width: 260,
    sortKey: "contact",
  },
  {
    key: "owner",
    labelKey: "interactions.column.owner",
    defaultVisible: true,
    width: 170,
    sortKey: "owner",
  },
  {
    key: "when",
    labelKey: "interactions.column.when",
    defaultVisible: true,
    width: 150,
    sortKey: "occurred_at",
  },
];
