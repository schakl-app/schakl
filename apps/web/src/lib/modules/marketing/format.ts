/**
 * Formatting a marketing metric for what it *is* (issue #134).
 *
 * Cost/revenue/conversion-value print as money in the *account's* own currency (it may differ
 * from the tenant's, #124 — we label it, never convert it); CTR/engagement are ratios shown as
 * percentages; average position is one decimal and lower is better; counts are whole numbers.
 * A delta's tone flips for a lower-is-better metric so an improving average position reads green.
 */
import { dateLocale, fmtMonthYear, fmtNumber, fmtPeriod } from "$lib/core/format";
import { t } from "$lib/core/i18n";

import type { CompareWindow, ComparePeriod } from "./types";

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

/** The setting's own name, for the two selects that configure it (#312). */
export function compareModeLabel(mode: ComparePeriod): string {
  return t(`marketing.compare.${mode}`);
}

const _LAST_DAY = (iso: string): number =>
  new Date(Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)), 0)).getUTCDate();

/**
 * Name a compared span the way a person would (#312).
 *
 * A whole calendar month is "juli 2025" and a whole year is "2025"; anything else falls back to
 * the shared date-range format ("11 jul – 9 aug 2025"). Naming the span rather than the *mode*
 * is the whole point of the issue: "t.o.v. vorige periode" was a label the screen could print
 * over any two dates at all, so a comparison set to the wrong thing looked exactly like one set
 * to the right thing. "t.o.v. juli 2025" is checkable at a glance.
 */
export function comparePeriodLabel(window: Pick<CompareWindow, "start" | "end">): string {
  const { start, end } = window;
  if (
    start.slice(0, 7) === end.slice(0, 7) &&
    start.endsWith("-01") &&
    Number(end.slice(8, 10)) === _LAST_DAY(end)
  ) {
    return fmtMonthYear(start.slice(0, 7));
  }
  if (start.endsWith("-01-01") && end.endsWith("-12-31") && start.slice(0, 4) === end.slice(0, 4)) {
    return start.slice(0, 4);
  }
  return fmtPeriod(start, end);
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
