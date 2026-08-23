/**
 * The urgency vocabulary — four buckets, defined once, read by everything that draws a task
 * (#395, #397).
 *
 * Two screens arrived at this module from opposite ends in the same week. The **dashboard tile**
 * partitioned into three buckets (overdue / today / everything else) and called the third one
 * *Binnenkort*, so this afternoon's week, next month and every task with no deadline at all were
 * one undifferentiated list. The **board** was twenty identical grey rows: it grouped by *status*
 * and ordered by whatever was last dragged, so the two overdue tasks sat at positions 5 and 7 and
 * this week's work was below a fortnight of later work because it happened to carry a different
 * status. The four buckets existed only as `?due=`, which is a *filter*: they could be asked for
 * one at a time and never seen at once.
 *
 * | bucket    | rule |
 * |-----------|------|
 * | `overdue` | `due_date < today` |
 * | `today`   | `due_date === today` |
 * | `week`    | after today, up to and including `today + 7` |
 * | `later`   | after that — and a task with no deadline at all |
 *
 * **"Deze week" is the next seven days.** This is the boundary the API's `?due=week` filter has
 * always meant (`tasks/service.py`), which is what lets a bucket heading link to the list it
 * totals and have the two counts agree — the tile links to `/tasks?due=week`, and the board draws
 * that same heading beside that same filter chip. A calendar week ending on Sunday reads better
 * on a Monday and collapses to nothing on a Friday afternoon, which is the day people plan; it
 * would also have needed the API, the filter chips and the tile to move with it, and a heading
 * that disagrees with the chip beside it is worse than either rule on its own.
 *
 * **The zone is the caller's to resolve, once.** Every function here takes `today` as a plain
 * `YYYY-MM-DD` argument, which is what `orgToday()` hands back: the tenant's calendar date, not
 * UTC's (#396) and not the Node process's. A helper that read the clock itself would be the
 * fifteenth call site of that bug, and it would be untestable besides.
 *
 * **One helper, seven surfaces.** The board, `TaskRow` (and through it the mobile board, the
 * project to-do and the client's Taken panel), the task card, the @mention picker and the My Day
 * tile each carried their own `due_date < today` comparison — the tile's was subtly different
 * from the list's — which is how a screen and the tile above it come to disagree about what is
 * urgent.
 */
// Relative, extension and all: node's test runner loads this file directly and knows neither the
// `$lib` alias nor extensionless ESM resolution. `isodate.ts` imports nothing, which is the whole
// reason it was split out of `calendar.ts` (whose i18n import drags the paraglide bundle in).
import { isoAddDays, isoDiffDays } from "../../core/isodate.ts";
import type { UiState } from "../../core/state.ts";

/** The four urgencies a task's deadline can have, most urgent first. */
export const DUE_BUCKETS = ["overdue", "today", "week", "later"] as const;
export type DueBucket = (typeof DUE_BUCKETS)[number];

/** Where "deze week" stops. The API's `?due=week` window, in days from today. */
export const WEEK_HORIZON = 7;

/**
 * The board's sections: the four buckets plus finished work.
 *
 * `done` is deliberately **not** a bucket — a finished task has no urgency, and filing one under
 * *Over tijd* in red because it was completed late is the loudest possible way to say nothing.
 * It is a section of the board, last and folded, exactly as the terminal statuses always were.
 */
export const DUE_SECTIONS = [...DUE_BUCKETS, "done"] as const;
export type DueSection = (typeof DUE_SECTIONS)[number];

/** Whole days from `from` to `to`, both date-only ISO strings. Negative when `to` is earlier. */
export const dayDistance = isoDiffDays;

/** The last day still counted as "deze week": seven days out, the API's own window. */
export function weekEnd(today: string): string {
  return isoAddDays(today, WEEK_HORIZON);
}

/**
 * Which bucket a deadline falls in, read against the tenant's `today`.
 *
 * `end` is a parameter so a caller partitioning a whole list computes it once rather than per
 * row — and so a test can name it.
 */
export function dueBucket(
  due: string | null | undefined,
  today: string,
  end: string = weekEnd(today),
): DueBucket {
  // No deadline is *later*, not *overdue*: it is work nobody has scheduled, and the release that
  // made the column required (#392) left every instance carrying rows written before the rule.
  // It has to be somewhere, or the four buckets are not a partition and a list silently drops
  // rows; `?undated=1` is how you go looking for them on purpose.
  if (!due) return "later";
  if (due < today) return "overdue";
  if (due === today) return "today";
  return due <= end ? "week" : "later";
}

/** The board's section for a row: its bucket, unless it is finished. */
export function dueSection(
  due: string | null | undefined,
  today: string,
  done: boolean,
  end: string = weekEnd(today),
): DueSection {
  return done ? "done" : dueBucket(due, today, end);
}

/**
 * Partition rows into the four buckets, keeping each bucket in the order it arrived — the API
 * already sorts by date and then priority, and re-sorting here would be a second opinion about
 * an order the list and the board also read.
 */
export function groupByDue<T extends { due_date?: string | null }>(
  rows: readonly T[],
  today: string,
): Record<DueBucket, T[]> {
  const groups: Record<DueBucket, T[]> = { overdue: [], today: [], week: [], later: [] };
  const end = weekEnd(today);
  for (const row of rows) groups[dueBucket(row.due_date, today, end)].push(row);
  return groups;
}

/**
 * The list this bucket is a total of. Same four names as `?due=`, so the destination confirms
 * where you landed and shows exactly the rows the heading counted.
 */
export function dueHref(bucket: DueBucket): string {
  return `/tasks?due=${bucket}`;
}

/** The heading's i18n key — the same words as the filter chip on the list it opens. */
export function dueLabelKey(bucket: DueBucket): string {
  return `tasks.due.${bucket}`;
}

/**
 * How each section heading is drawn — the state palette's vocabulary (#404), never a hue
 * invented here, and `null` where the honest answer is *no state at all*.
 *
 * Two of these were decided by looking at the screen rather than by reasoning about it.
 *
 * The issue asked for "vandaag" in the **brand** colour. The palette bars that outright, and
 * this exact board is why: on the tenant whose brand is gold it would render beside the red
 * *Over tijd* as a second warning, and on a blue-branded one as a link.
 *
 * And *Deze week* was `soon` — defensible from the palette's own definition ("approaching, and
 * worth knowing about") and wrong on the page. At 10px uppercase, red, orange and amber do not
 * separate: three warm headings down one board read as one long warning, which is the *"rustiger
 * gebruik van kleuren"* half of the complaint arriving as its own answer. So only the two states
 * that are genuinely claims are tinted — the moment has passed, and the moment is now — and the
 * rest of the week is `neutral`, which is the theme's own text: stronger than the muted grey
 * *Later* keeps, and not a fourth hue competing for the same attention. The tile reads the same
 * record for the same reason it reads the same boundaries.
 *
 * Only the **heading** is tinted at all. Twenty tinted rows would be worse than twenty grey
 * ones: the hierarchy the team asked for is *between* the groups, so the rows stay quiet.
 */
export const DUE_STATE: Record<DueSection, UiState | null> = {
  overdue: "late",
  today: "today",
  // Neutral, but *drawn*: a section carrying a state renders in the theme's own text, and one
  // carrying none renders in the muted grey every grouped list has always used.
  week: "neutral",
  later: null,
  done: null,
};

/** How loudly a bucket is drawn. One record, so a tile and the board cannot disagree. */
export function dueState(bucket: DueBucket): UiState {
  return DUE_STATE[bucket] ?? "neutral";
}

/**
 * How far a deadline is from today, as an i18n key and its count.
 *
 * `18 aug` alone asks the reader to know today's date and do the arithmetic; `18 aug · 3 dagen
 * te laat` does not. Relative *only* would be worse — it cannot be matched against a calendar —
 * so both are printed, with the relative half muted (`DueDate.svelte`).
 *
 * Returned as `{ key, count }` rather than a string because this module may not import i18n:
 * `t()` reaches the generated paraglide bundle, which is exactly what would make the rule below
 * untestable. The keys are `_one` / `_other` pairs and never ICU plurals — Paraglide does not
 * parse those here, and a broken plural compiles to garbage rather than failing.
 */
export function dueDistance(due: string, today: string): { key: string; count: number } | null {
  const days = isoDiffDays(today, due);
  if (days === 0) return { key: "tasks.due.rel.today", count: 0 };
  if (days === 1) return { key: "tasks.due.rel.tomorrow", count: 1 };
  if (days < 0) {
    const late = -days;
    return { key: late === 1 ? "tasks.due.rel.late_one" : "tasks.due.rel.late_other", count: late };
  }
  return { key: "tasks.due.rel.in_days", count: days };
}
