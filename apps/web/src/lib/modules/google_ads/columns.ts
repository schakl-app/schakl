/**
 * What each report shows, and how each value is rendered.
 *
 * One list per view rather than five near-identical tables: every read returns the same
 * envelope, so the only thing that differs is which keys to draw and how. The `kind` is what
 * carries the two integrity rules out of the API and onto the screen — a `ratio` is multiplied
 * by 100 exactly once, and a `null` is drawn as a dash rather than a zero.
 */
import { t } from "$lib/core/i18n";

export type ReportView = "campaigns" | "keywords" | "search-terms" | "negatives" | "changes";

export type ColumnKind =
  | "text"
  /** Money in the account's own currency — never assumed to be EUR. */
  | "money"
  | "number"
  /** A fraction from the API, shown as a percentage. */
  | "ratio"
  /** An enum name from Google, translated through `google_ads.enum.*` with a fallback. */
  | "enum"
  | "datetime"
  /** The old → new list on a change-history row. */
  | "changes";

export interface ReportColumn {
  key: string;
  label: () => string;
  kind: ColumnKind;
  /** Right-aligned in the table, as every numeric column should be. */
  numeric?: boolean;
}

const metric = (key: string, kind: ColumnKind = "number"): ReportColumn => ({
  key,
  label: () => t(`google_ads.metric.${key}`),
  kind,
  numeric: true,
});

const METRICS: ReportColumn[] = [
  metric("impressions"),
  metric("clicks"),
  metric("cost", "money"),
  metric("ctr", "ratio"),
  metric("average_cpc", "money"),
  metric("conversions"),
  metric("cost_per_conversion", "money"),
];

export const COLUMNS: Record<ReportView, ReportColumn[]> = {
  campaigns: [
    { key: "campaign_name", label: () => t("google_ads.column.campaign"), kind: "text" },
    { key: "status", label: () => t("google_ads.column.status"), kind: "enum" },
    { key: "channel_type", label: () => t("google_ads.column.channel"), kind: "enum" },
    { key: "daily_budget", label: () => t("google_ads.column.budget"), kind: "money", numeric: true },
    ...METRICS,
    // Only Search-like campaigns report these; elsewhere they are null, which is not 0 %.
    {
      key: "search_impression_share",
      label: () => t("google_ads.metric.search_impression_share"),
      kind: "ratio",
      numeric: true,
    },
    {
      key: "search_lost_is_budget",
      label: () => t("google_ads.metric.search_lost_is_budget"),
      kind: "ratio",
      numeric: true,
    },
  ],
  keywords: [
    { key: "keyword", label: () => t("google_ads.column.keyword"), kind: "text" },
    { key: "match_type", label: () => t("google_ads.column.match_type"), kind: "enum" },
    { key: "campaign_name", label: () => t("google_ads.column.campaign"), kind: "text" },
    {
      key: "quality_score",
      label: () => t("google_ads.column.quality_score"),
      kind: "number",
      numeric: true,
    },
    ...METRICS,
  ],
  "search-terms": [
    { key: "search_term", label: () => t("google_ads.column.search_term"), kind: "text" },
    { key: "match_status", label: () => t("google_ads.column.match_status"), kind: "enum" },
    { key: "campaign_name", label: () => t("google_ads.column.campaign"), kind: "text" },
    ...METRICS,
  ],
  negatives: [
    { key: "keyword", label: () => t("google_ads.column.keyword"), kind: "text" },
    { key: "match_type", label: () => t("google_ads.column.match_type"), kind: "enum" },
    { key: "level", label: () => t("google_ads.column.level"), kind: "enum" },
    { key: "campaign_name", label: () => t("google_ads.column.campaign"), kind: "text" },
    { key: "shared_set_name", label: () => t("google_ads.column.shared_set"), kind: "text" },
  ],
  changes: [
    { key: "changed_at", label: () => t("google_ads.column.changed_at"), kind: "datetime" },
    { key: "resource_type", label: () => t("google_ads.column.resource"), kind: "enum" },
    { key: "operation", label: () => t("google_ads.column.operation"), kind: "enum" },
    { key: "changed_by", label: () => t("google_ads.column.changed_by"), kind: "text" },
    { key: "changed_fields", label: () => t("google_ads.column.what_changed"), kind: "changes" },
  ],
};
