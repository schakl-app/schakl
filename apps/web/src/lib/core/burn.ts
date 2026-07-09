/**
 * The one budget-burn scale (docs/UX.md): green < 75 %, amber < 100 %, red ≥ 100 %.
 *
 * The scale was documented long before it existed. The only burn bar in the app was
 * brand-vs-red and clamped its width at 100 %, so a project 40 % over budget looked exactly
 * like one that had just landed on it. Both the percentage and the colour live here now, and
 * every surface that shows burn reads them from here.
 */

export type BurnLevel = "ok" | "warn" | "over";

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

/** Bar fill. Amber and red are the semantic colours; below 75 % the tenant's brand carries it. */
export function burnBarClass(pct: number | null): string {
  switch (burnLevel(pct)) {
    case "over":
      return "bg-red-500 dark:bg-red-400";
    case "warn":
      return "bg-amber-500 dark:bg-amber-400";
    case "ok":
      return "bg-brand";
    default:
      return "bg-transparent";
  }
}

/** Text colour for the remaining figure. Only "over" shouts; the rest stay quiet. */
export function burnTextClass(pct: number | null): string {
  return burnLevel(pct) === "over" ? "text-red-600 dark:text-red-400" : "text-text";
}

/** Width of the drawn bar. Clamped, unlike the number beside it. */
export function burnBarWidth(pct: number | null): number {
  if (pct == null) return 0;
  return Math.max(0, Math.min(100, pct));
}
