/**
 * Column descriptors for the cross-client marketing grid (Overzicht → Marketing, issue #133).
 *
 * `sortKey` is present only where the API can order by it (mirrors the overview endpoint's `sort`
 * allow-list): a header that claims to sort and doesn't is worse than a quiet one (docs/UX.md).
 * The metric columns carry a delta beside the value, so the morning read is "who moved".
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const MARKETING_OVERVIEW_TABLE_ID = "marketing_overview";

export const MARKETING_OVERVIEW_COLUMNS: ColumnMeta[] = [
  {
    key: "company",
    labelKey: "marketing.overview.column.company",
    sortKey: "company_name",
    primary: true,
    width: 220,
  },
  { key: "sources", labelKey: "marketing.overview.column.sources", width: 120 },
  {
    key: "sessions",
    labelKey: "marketing.overview.column.sessions",
    sortKey: "sessions",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "clicks",
    labelKey: "marketing.overview.column.clicks",
    sortKey: "clicks",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "position",
    labelKey: "marketing.overview.column.position",
    sortKey: "position",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "cost",
    labelKey: "marketing.overview.column.cost",
    sortKey: "cost",
    align: "right",
    defaultVisible: true,
  },
  {
    key: "conversions",
    labelKey: "marketing.overview.column.conversions",
    sortKey: "conversions",
    align: "right",
    defaultVisible: true,
  },
];
