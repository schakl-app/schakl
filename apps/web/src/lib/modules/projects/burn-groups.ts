/**
 * The burn partition the dashboard's budget tile draws — three bands of the one burn scale
 * (`core/burn.ts`), each with the state it is drawn in, the heading it is called by and the
 * list it opens.
 *
 * Pure on purpose, the shape of `modules/tasks/due.ts`: no Svelte, no i18n runtime, so a plain
 * node test can pin the two facts a screenshot cannot — that every budgeted row lands in exactly
 * one band, and that a band's URL token is the one the API's `?burn=` filter and the list page's
 * pill both speak. The API counts the bands over the whole set (`DashboardBudgets`); this file
 * only sorts the rows that arrived into them, so a heading's number is always the API's.
 */
import { burnLevel, burnPct, type BurnLevel } from "../../core/burn.ts";
import type { HoursFields } from "../../core/hours.ts";
import type { UiState } from "../../core/state.ts";

/** Hottest band first — the order the tile draws them in and the order the API sorts rows by. */
export const BURN_GROUPS: readonly BurnLevel[] = ["over", "warn", "ok"] as const;

/** Over budget is a claim (`late`), almost spent is worth knowing (`soon`), room is fine. */
export const BURN_GROUP_STATE: Record<BurnLevel, UiState> = {
  over: "late",
  warn: "soon",
  ok: "ok",
};

/** The heading's key. The list page's pill reads the same key, so the destination confirms. */
export function burnGroupLabelKey(level: BurnLevel): string {
  return `projects.filter.burn.${level}`;
}

/**
 * The list a band's heading opens. `status=active` because the tile counts over *active*
 * budgeted projects — a figure of 4 opening a list of 5 is the disagreement docs/UX.md
 * Principle 7 forbids — and the `burn` token is the API's own, never a word of ours.
 */
export function burnGroupHref(level: BurnLevel): string {
  return `/projects?burn=${level}&status=active`;
}

/**
 * A `?burn=` value off a query string anyone can edit, narrowed to the three tokens the API
 * honours. Anything else is `undefined` — the filter falls back rather than 422s (CLAUDE.md §9),
 * and the list page and its export read the same answer so the spreadsheet cannot disagree
 * with the screen.
 */
export function burnFilterToken(value: string | null | undefined): BurnLevel | undefined {
  return (BURN_GROUPS as readonly string[]).includes(value ?? "")
    ? (value as BurnLevel)
    : undefined;
}

export interface BurnRow {
  id: string;
  hours?: HoursFields | null;
}

/** Every budgeted row into exactly one band, in the order it arrived. Unbudgeted rows drop. */
export function groupByBurn<T extends BurnRow>(rows: T[]): Record<BurnLevel, T[]> {
  const groups: Record<BurnLevel, T[]> = { over: [], warn: [], ok: [] };
  for (const row of rows) {
    const level = burnLevel(burnPct(row.hours?.spent_hours ?? 0, row.hours?.budget_hours ?? null));
    if (level) groups[level].push(row);
  }
  return groups;
}
