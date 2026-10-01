/**
 * The planner's columns (docs/UX.md: every list is the shared `DataTable`, driven by column
 * descriptors) and the keys its URL is narrowed by.
 *
 * Every column but the post itself states a `width`, because the table lays out `table-fixed`:
 * an undeclared width is an equal share of the slack, not "as wide as it needs".
 *
 * A `sortKey` is declared exactly where `GET /meta-business/posts?sort=` can order by the
 * column. The client is named through the directory seam, not a column the query can order on,
 * so its header is not clickable.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const POSTS_TABLE_ID = "meta_posts";

export const POST_COLUMNS: ColumnMeta[] = [
  { key: "post", labelKey: "meta.list.post", primary: true },
  { key: "company", labelKey: "meta.list.client", defaultVisible: true, width: 190 },
  { key: "channels", labelKey: "meta.list.channels", defaultVisible: true, width: 150 },
  {
    key: "when",
    labelKey: "meta.list.when",
    sortKey: "when",
    defaultVisible: true,
    width: 190,
  },
  // Wide enough for the longest label this vocabulary holds ("Wacht op goedkeuring"), not for
  // the one on the screen it was measured on (#347).
  {
    key: "status",
    labelKey: "meta.list.status",
    sortKey: "status",
    defaultVisible: true,
    width: 200,
  },
  { key: "author", labelKey: "meta.list.author", defaultVisible: false, width: 180 },
  { key: "approver", labelKey: "meta.list.approver", defaultVisible: false, width: 180 },
  {
    key: "created",
    labelKey: "meta.list.created",
    sortKey: "created",
    defaultVisible: false,
    width: 170,
  },
];

/** What the planner can be narrowed by: the keys the URL carries and the bar renders. */
export const POST_FILTERS = ["q", "company", "channel", "status"] as const;

export type PostFilterKey = (typeof POST_FILTERS)[number];

/** The URL token for "every status, the archive included" — a view owes itself a link (#329). */
export const STATUS_ALL = "all";

/**
 * The channels register: what a token reaches and whose each is. Not sortable — it reads
 * alphabetically, which is the order somebody looking for a client's Page looks in.
 */
export const CHANNEL_COLUMNS: ColumnMeta[] = [
  { key: "asset", labelKey: "meta.channels.column_asset", primary: true },
  { key: "kind", labelKey: "meta.channels.column_kind", defaultVisible: true, width: 190 },
  { key: "client", labelKey: "meta.list.client", defaultVisible: true, width: 220 },
  { key: "relation", labelKey: "meta.channels.column_relation", defaultVisible: true, width: 170 },
  { key: "state", labelKey: "meta.list.status", defaultVisible: true, width: 240 },
];
