/**
 * The urgency partition a dashboard row is drawn from (#398).
 *
 * The team's complaint was that "Openstaande taken per project / klant" showed a name and a
 * number, and a number says how *much* work a client is carrying while saying nothing at all
 * about whether any of it is late — so the tile ranked five comfortable tasks above one that
 * was due last Tuesday, and the two rows that had something overdue sat at positions 3 and 11.
 *
 * Three rules live here rather than in the widget, because each one fails silently in a browser:
 *
 * 1. **A bucket is a `?due=` chip's own set.** `late → overdue`, `today → today`,
 *    `soon → week` — swap two and every counter still renders, still links and still opens a
 *    plausible list, and nobody notices until they count. The API's three aggregates are the
 *    same three predicates (`app/modules/tasks/service.py`), so the figure and the list it
 *    opens agree by construction (docs/UX.md, principle 7).
 * 2. **A zero draws nothing.** Four zeros on a row is four facts nobody asked for, and the
 *    whole point of the row is that it can be scanned.
 * 3. **The order is the ramp**, late → today → soon, because that is the order the reader's eye
 *    is being asked to take them in.
 *
 * The colours are the state palette's (`$lib/core/state.ts`, #404) and never the tenant's brand.
 */
import type { UiState } from "$lib/core/state";

/** The three figures beside the total, as the tile receives them. */
export interface UrgencyCounts {
  overdue: number;
  due_today: number;
  due_week: number;
}

export interface UrgencyCounter {
  /** How it is drawn (`$lib/core/state.ts`). */
  state: UiState;
  /** The message key, taking `{count}`. */
  key: string;
  count: number;
  /** The `?due=` value whose list holds exactly these tasks. */
  due: "overdue" | "today" | "week";
}

const BUCKETS: {
  state: UiState;
  key: string;
  due: UrgencyCounter["due"];
  of: keyof UrgencyCounts;
}[] = [
  { state: "late", key: "tasks.overdue_count", due: "overdue", of: "overdue" },
  { state: "today", key: "tasks.due_today_count", due: "today", of: "due_today" },
  { state: "soon", key: "tasks.due_week_count", due: "week", of: "due_week" },
];

/** The counters this row should draw, in urgency order, zeros dropped. */
export function urgencyCounters(counts: Partial<UrgencyCounts> | null | undefined) {
  return BUCKETS.map((bucket) => ({
    state: bucket.state,
    key: bucket.key,
    due: bucket.due,
    count: counts?.[bucket.of] ?? 0,
  })).filter((counter): counter is UrgencyCounter => counter.count > 0);
}
