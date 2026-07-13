/**
 * The columns the Interacties list can show (#168, #24).
 *
 * The API's list has exactly one order — the timeline, `occurred_at desc` — so no column
 * carries a `sortKey`: a header that claims to sort and doesn't is worse than a quiet one
 * (docs/UX.md). Day sections and chronology therefore always agree, like the inbox.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const INTERACTIONS_TABLE_ID = "interactions";

export const INTERACTION_COLUMNS: ColumnMeta[] = [
  { key: "subject", labelKey: "interactions.column.subject", primary: true, width: 380 },
  { key: "kind", labelKey: "interactions.column.kind", defaultVisible: true, width: 170 },
  { key: "linked", labelKey: "interactions.column.linked", defaultVisible: true, width: 260 },
  { key: "owner", labelKey: "interactions.column.owner", defaultVisible: true, width: 170 },
  { key: "when", labelKey: "interactions.column.when", defaultVisible: true, width: 150 },
];
