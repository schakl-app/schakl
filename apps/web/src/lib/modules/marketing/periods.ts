/**
 * The period vocabulary the dashboard offers (#316) — mirrors `app.core.periods`.
 *
 * Two kinds of token, and the difference is the whole design:
 *
 * - **Rolling presets** (`30d`, `month`, `last_month`, `quarter`, `yoy`) mean something different
 *   every day. They belong in the tab row, and a bookmark of one still says what its owner meant
 *   next month.
 * - **Named calendar periods** (`2026-07`, `2026-Q3`) are frozen. They belong in the picker, and a
 *   link to one shows the same numbers in a year's time — which is what makes it worth sending to
 *   a colleague.
 *
 * Nothing here resolves a token to dates — that happens once, in the API (#312). What this file
 * does is decide which months and quarters are worth *offering*, and even that is anchored on the
 * **tenant's** calendar rather than the browser's: `anchorMonth()` reads the org zone off the same
 * plumbing `format.ts` uses, so a browser in Lisbon and a browser in Warsaw offer the same list on
 * the two days a year they would otherwise disagree.
 */
import { fmtMonthYear } from "$lib/core/format";
import { t } from "$lib/core/i18n";
import { getTimeZone } from "$lib/core/timezone";

import { monthTokens, quarterTokens } from "./period-tokens";

/** The rolling presets, in the order the tab row shows them. */
export const PERIOD_PRESETS = ["30d", "90d", "month", "last_month", "quarter", "yoy"] as const;

export interface PeriodOption {
  token: string;
  label: string;
}

const _quarterLabel = (year: number, quarter: number): string =>
  t("marketing.period.quarter", { quarter: String(quarter), year: String(year) });

/**
 * Today in the tenant's timezone, as `YYYY-MM-DD` — the newest period worth offering.
 *
 * The current month is offered because asking for it is meaningful: the API clamps a month in
 * progress to the last complete day, so "2026-08" on the 10th is the same nine days "deze maand"
 * would have shown. What it must never do is offer a month that has not begun where the tenant
 * lives, which is the whole reason this reads the org zone instead of `new Date().getMonth()`.
 */
export function anchorMonth(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: getTimeZone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  return parts; // en-CA formats as YYYY-MM-DD
}

/** The named months to offer, newest first, starting with the month `anchor` falls in. */
export function monthOptions(anchor: string): PeriodOption[] {
  return monthTokens(anchor).map((token) => ({ token, label: fmtMonthYear(token) }));
}

/** The named quarters to offer, newest first, starting with the quarter `anchor` falls in. */
export function quarterOptions(anchor: string): PeriodOption[] {
  return quarterTokens(anchor).map((token) => ({
    token,
    label: _quarterLabel(Number(token.slice(0, 4)), Number(token.slice(6))),
  }));
}
