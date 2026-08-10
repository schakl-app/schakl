/**
 * Which calendar periods the picker offers, as tokens (#316).
 *
 * Split out from `periods.ts` and deliberately dependency-free — no `$lib`, no i18n, no clock —
 * because this is the half that is *arithmetic*, and the arithmetic is where it goes wrong: both
 * walks step backwards across a year boundary, where the naive `year--, unit--` produces "Q0" and
 * "month 0" on exactly the two inputs nobody demonstrates. Labelling is a separate concern and
 * lives with the locale plumbing; this file is the one a unit test can import.
 *
 * Tokens match `app.core.periods`: `YYYY-MM` for a month, `YYYY-Qn` for a quarter.
 */

/** How many named months and quarters to offer. Enough to reach the same month a year back. */
export const MONTH_OPTIONS = 15;
export const QUARTER_OPTIONS = 6;

/** The `YYYY-MM` tokens to offer, newest first, starting with the month `anchor` falls in. */
export function monthTokens(anchor: string, count: number = MONTH_OPTIONS): string[] {
  const year = Number(anchor.slice(0, 4));
  const month = Number(anchor.slice(5, 7));
  const out: string[] = [];
  for (let back = 0; back < count; back++) {
    // Floor division rather than a loop of decrements: `shifted` goes negative the moment the
    // walk crosses January, and `%` in JS keeps the sign of the dividend.
    const shifted = month - 1 - back;
    const y = year + Math.floor(shifted / 12);
    const m = ((shifted % 12) + 12) % 12;
    out.push(`${y}-${String(m + 1).padStart(2, "0")}`);
  }
  return out;
}

/** The `YYYY-Qn` tokens to offer, newest first, starting with the quarter `anchor` falls in. */
export function quarterTokens(anchor: string, count: number = QUARTER_OPTIONS): string[] {
  const year = Number(anchor.slice(0, 4));
  const quarter = quarterOf(anchor);
  const out: string[] = [];
  for (let back = 0; back < count; back++) {
    const shifted = quarter - 1 - back;
    const y = year + Math.floor(shifted / 4);
    const q = (((shifted % 4) + 4) % 4) + 1;
    out.push(`${y}-Q${q}`);
  }
  return out;
}

/** The quarter (1-4) a `YYYY-MM…` token or ISO date falls in. */
export function quarterOf(iso: string): number {
  return Math.floor((Number(iso.slice(5, 7)) - 1) / 3) + 1;
}
