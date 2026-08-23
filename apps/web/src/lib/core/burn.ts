/**
 * The one budget-burn scale (docs/UX.md): green < 75 %, amber < 100 %, red ≥ 100 %.
 *
 * The scale was documented long before it existed. The only burn bar in the app was
 * brand-vs-red and clamped its width at 100 %, so a project 40 % over budget looked exactly
 * like one that had just landed on it. Both the percentage and the colour live here now, and
 * every surface that shows burn reads them from here.
 *
 * The **colours** moved out again in #404: a burn level is a semantic state, and the palette
 * for those is `core/state.ts`. Two things changed in the move and both were bugs. The scale
 * had documented "green" for years and drawn `bg-brand` — so on the tenant whose brand is
 * gold, a project comfortably inside its budget was drawn in a colour indistinguishable from
 * the amber one step above it; a state may never be the tenant's brand, and this was the
 * oldest breach of that rule in the app. And "bad" was `text-red-600` here against
 * `text-red-700` in `SummaryStrip`, two shades of the same claim inside `lib/core` alone.
 * What stays here is what is genuinely this module's: where the thresholds sit.
 */

import { stateFillClass, stateTextClass, type UiState } from "./state.ts";

export type BurnLevel = "ok" | "warn" | "over";

/**
 * The burn level said as a state. `warn` is `soon` rather than `today`: three quarters spent
 * is a thing to know before it becomes a problem, which is exactly what `soon` names.
 */
export function burnState(pct: number | null): UiState {
  switch (burnLevel(pct)) {
    case "over":
      return "late";
    case "warn":
      return "soon";
    case "ok":
      return "ok";
    default:
      return "neutral";
  }
}

/**
 * Percentage of the budget consumed. **Unclamped** — 130 means 30 % over, and callers that draw
 * a bar are the ones responsible for clamping its *width*. Returns `null` when there is no
 * budget to burn (an em-dash, not a zero: nothing was consumed *of nothing*).
 */
export function burnPct(spent: number, budget: number | null | undefined): number | null {
  if (budget == null || budget <= 0) return null;
  return (spent / budget) * 100;
}

export function burnLevel(pct: number | null): BurnLevel | null {
  if (pct == null) return null;
  if (pct >= 100) return "over";
  if (pct >= 75) return "warn";
  return "ok";
}

/** Bar fill, from the state palette — never the brand, whatever the level (#404). */
export function burnBarClass(pct: number | null): string {
  return pct == null ? "bg-transparent" : stateFillClass(burnState(pct));
}

/**
 * Text colour for the remaining figure. Only "over" shouts; the rest stay quiet — a figure
 * that is fine says so with the bar beside it, and colouring every remainder green would spend
 * the reader's attention on the rows with nothing to report.
 */
export function burnTextClass(pct: number | null): string {
  return burnLevel(pct) === "over" ? stateTextClass("late") : "text-text";
}

/** Width of the drawn bar. Clamped, unlike the number beside it. */
export function burnBarWidth(pct: number | null): number {
  if (pct == null) return 0;
  return Math.max(0, Math.min(100, pct));
}
