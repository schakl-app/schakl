/**
 * Formatting a marketing metric for what it *is* (issue #134).
 *
 * Cost/revenue/conversion-value print as money in the *account's* own currency (it may differ
 * from the tenant's, #124 — we label it, never convert it); CTR/engagement are ratios shown as
 * percentages; average position is one decimal and lower is better; counts are whole numbers.
 * A delta's tone flips for a lower-is-better metric so an improving average position reads green.
 */
import { dateLocale, fmtNumber } from "$lib/core/format";
import { t } from "$lib/core/i18n";

const MONEY_METRICS = new Set(["cost", "totalRevenue", "conversionsValue"]);
const PERCENT_METRICS = new Set(["ctr", "engagementRate"]);

export function sourceLabel(source: string): string {
  return t(`marketing.source.${source}`);
}

export function metricLabel(key: string): string {
  return t(`marketing.metric.${key}`);
}

/** A tile's display label: the client's override in the viewer's locale (#192), else the
 *  built-in metric label. Overrides are tenant data ({nl, en}), so fall through sensibly. */
export function tileLabel(
  key: string,
  overrides?: Record<string, Record<string, string>> | null,
): string {
  const override = overrides?.[key];
  if (override) {
    const locale = dateLocale().startsWith("nl") ? "nl" : "en";
    return override[locale] || override.nl || override.en || metricLabel(key);
  }
  return metricLabel(key);
}

export function drilldownLabel(kind: string): string {
  return t(`marketing.drilldown.${kind}`);
}

export function fmtCurrency(value: number, currency: string | null | undefined): string {
  return new Intl.NumberFormat(dateLocale(), {
    style: "currency",
    currency: currency || "EUR",
    trailingZeroDisplay: "stripIfInteger",
  }).format(value);
}

export function fmtPercent(value: number): string {
  return new Intl.NumberFormat(dateLocale(), {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

/** Format a metric value by its key (money / percent / position / count). */
export function fmtMetric(key: string, value: number, currency?: string | null): string {
  if (MONEY_METRICS.has(key)) return fmtCurrency(value, currency);
  if (PERCENT_METRICS.has(key)) return fmtPercent(value);
  if (key === "position" || key === "position_change") return fmtNumber(value, 1);
  return fmtNumber(value, 0);
}

export interface DeltaView {
  text: string;
  tone: "up" | "down" | "flat";
}

/** A period-over-period delta as a signed % plus its good/bad tone (null when incomparable). */
export function deltaView(
  deltaPct: number | null | undefined,
  lowerIsBetter = false,
): DeltaView | null {
  if (deltaPct === null || deltaPct === undefined) return null;
  const sign = deltaPct > 0 ? "+" : "";
  const text = `${sign}${fmtNumber(deltaPct, 1)}%`;
  let tone: "up" | "down" | "flat" = "flat";
  if (deltaPct > 0) tone = lowerIsBetter ? "down" : "up";
  else if (deltaPct < 0) tone = lowerIsBetter ? "up" : "down";
  return { text, tone };
}

/** The Tailwind text colour for a delta tone (semantic, theme-aware via the token). */
export function deltaClass(tone: "up" | "down" | "flat"): string {
  if (tone === "up") return "text-green-600 dark:text-green-400";
  if (tone === "down") return "text-red-600 dark:text-red-400";
  return "text-text-muted";
}

/** The health badge's Tailwind classes. */
export function healthClass(health: string): string {
  switch (health) {
    case "ok":
      return "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300";
    case "error":
    case "disconnected":
      return "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300";
    default:
      return "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
  }
}
