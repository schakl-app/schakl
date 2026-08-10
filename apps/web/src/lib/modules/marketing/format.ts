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

/**
 * One GA4 acquisition channel, in the reader's language — or exactly what Google called it.
 *
 * `sessionDefaultChannelGroup` is a fixed vocabulary Google defines: not tenant data, and not a
 * name anybody here chose. It was printing verbatim — a dashboard row reading *Unassigned* next
 * to one reading *Verwijzend verkeer*, and the same English words in the middle of a Dutch
 * client report. The catalogue answers for the ones we know; anything else keeps Google's own
 * string, because `t()` returns the *key* on a miss and `marketing.channel.audio_streaming` is
 * a worse thing to print than the English name it stood in for. The API resolves the same key
 * for the printed document (`reporting/render/context.channel_label`), so the screen and the
 * PDF say the same word.
 */
export function channelLabel(name: string): string {
  const raw = String(name ?? "");
  if (!raw) return raw;
  const key = `marketing.channel.${raw.toLowerCase().replace(/[-\s]+/g, "_")}`;
  const label = t(key);
  return label === key ? raw : label;
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

/** The quarter (1-4) an ISO date falls in, or 0 when it is not a quarter's first/last day. */
const _QUARTER_EDGE = (iso: string, edge: "start" | "end"): number => {
  const month = Number(iso.slice(5, 7));
  const day = Number(iso.slice(8, 10));
  if (edge === "start") return day === 1 && month % 3 === 1 ? (month - 1) / 3 + 1 : 0;
  return day === _LAST_DAY(iso) && month % 3 === 0 ? month / 3 : 0;
};

/**
 * Name a span the way a person would (#312, #316).
 *
 * A whole calendar month is "juli 2025", a whole quarter "Q3 2025", a whole year "2025"; anything
 * else falls back to the shared date-range format ("11 jul - 9 aug 2025"). Naming the span rather
 * than the *mode* is the whole point of #312: "t.o.v. vorige periode" was a label the screen could
 * print over any two dates at all, so a comparison set to the wrong thing looked exactly like one
 * set to the right thing. "t.o.v. juli 2025" is checkable at a glance.
 *
 * It reads **dates**, never the token that produced them, so it names the current period and the
 * compared one with the same function — and a month picked as "2025-07" and the same month
 * arriving as "last_month" can never print differently.
 */
export function periodLabel(window: { start: string; end: string }): string {
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
  const quarter = _QUARTER_EDGE(start, "start");
  if (quarter && quarter === _QUARTER_EDGE(end, "end") && start.slice(0, 4) === end.slice(0, 4)) {
    return t("marketing.period.quarter", { quarter: String(quarter), year: start.slice(0, 4) });
  }
  return fmtPeriod(start, end);
}

/** The span a payload's deltas were measured against. */
export function comparePeriodLabel(window: Pick<CompareWindow, "start" | "end">): string {
  return periodLabel(window);
}

/** The span a payload's numbers themselves cover (#316) — what the picker's summary names. */
export function currentPeriodLabel(
  window: Pick<CompareWindow, "current_start" | "current_end">,
): string {
  return periodLabel({ start: window.current_start, end: window.current_end });
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
