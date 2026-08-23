/**
 * What "vandaag", "deze week" and "later" mean — one definition, for every task surface (#397).
 *
 * The dashboard tile partitioned into **three** buckets (overdue / today / everything else) and
 * called the third one *Binnenkort*, so this afternoon's week, next month and every task with no
 * deadline at all were one undifferentiated list of identical grey rows. The team asked for today
 * to be the tile's subject with the week and the rest visually separated from it, which the
 * three-bucket partition cannot express.
 *
 * The reason this is a module rather than four `filter()` calls in the widget is the sibling
 * issue (#395, the task board): two surfaces that each decide for themselves where "deze week"
 * ends will disagree, and a reader moving from the tile to the board would find a task under a
 * different heading with nothing on either screen explaining why. So the buckets are declared
 * once here, and the API's `?due=` vocabulary uses **the same four names with the same
 * boundaries** — which is what lets a bucket heading link to the list it totals (docs/UX.md,
 * "nothing on a dashboard tile is a dead end") and have the two counts agree.
 *
 * Two boundary decisions are worth stating because neither is the only possible answer.
 *
 * **"Deze week" is the next seven days, not the calendar week.** A calendar week collapses to
 * nothing on a Friday afternoon — the bucket the tile most needs on the day people plan is the
 * day it is emptiest — and it makes the same task move between headings overnight for a reason
 * that has nothing to do with the task. Seven rolling days is also what the API's `?due=week`
 * filter has always meant, so this is the existing answer written down rather than a new one.
 *
 * **A task with no due date is "later".** It has to be somewhere or the four buckets are not a
 * partition and the tile silently drops rows (which is what the old `upcoming` did, by folding
 * them in beside next Tuesday's work). Least urgent is the honest place for work nobody has
 * committed to a day; `?undated=1` (#392) is how you go looking for them on purpose.
 *
 * Deliberately dependency-free — no `$lib`, no i18n runtime — so node's test runner can load it
 * (`tests/unit/task-due.test.ts`; `today.ts` keeps its relative import for the same reason). The
 * day arithmetic below is `core/calendar.isoDiffDays`' arithmetic; it is restated rather than
 * imported because that module pulls in the Paraglide message runtime, and a bucket boundary
 * that cannot be pinned by a test is exactly the kind that drifts.
 */

/** The four urgencies a task's deadline can have, most urgent first. */
export const DUE_BUCKETS = ["overdue", "today", "week", "later"] as const;

export type DueBucket = (typeof DUE_BUCKETS)[number];

/** Where "deze week" stops. The API's `?due=week` window, in days from today. */
export const WEEK_HORIZON = 7;

/** Whole days from `from` to `to`, both date-only ISO strings. Negative when `to` is earlier. */
export function dayDistance(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** Which bucket a deadline falls in, relative to the tenant's today (`$lib/core/today`). */
export function dueBucket(dueDate: string | null | undefined, today: string): DueBucket {
  if (!dueDate) return "later";
  const days = dayDistance(today, dueDate);
  if (days < 0) return "overdue";
  if (days === 0) return "today";
  return days <= WEEK_HORIZON ? "week" : "later";
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
  for (const row of rows) groups[dueBucket(row.due_date, today)].push(row);
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
 * How loudly a bucket is drawn (`$lib/core/state`). `later` is `neutral` on purpose: the palette
 * bars a fourth hue for "nothing is wrong here", and three tinted headings above a quiet one is
 * the hierarchy this issue asks for — colouring the rows instead would be noise.
 */
export function dueState(bucket: DueBucket): "late" | "today" | "soon" | "neutral" {
  if (bucket === "overdue") return "late";
  if (bucket === "today") return "today";
  return bucket === "week" ? "soon" : "neutral";
}
