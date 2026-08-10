/**
 * A task's hour budget, resolved once for every surface that draws it (#313).
 *
 * Four screens show the same burn — the task card, the list column, the compact row, and the
 * time module's entry form — and before this they each rebuilt the figures and the sentence
 * themselves. That is how the card ended up with its own 75/100 ladder; a shared component
 * (`core/ui/BudgetBar.svelte`) only fixes half of it if the numbers feeding it are still
 * derived four times.
 *
 * `logged_minutes` is **absent, not zero**, for a caller who may not read hours (the API gates
 * it on `time.entry.read`, and the seeded `client` role never holds it) — so `null` here means
 * "no burn to draw", and the caller falls back to the plain allocation it always showed. That
 * is the client-side half of the gate: §15's rule is that the web mirrors the key the API
 * actually checks, and here the API answers by omission, which is a mirror we get for free.
 *
 * The entry form lives in the `time` module and imports this, the way it already imports
 * `formatMinutes` from there in the other direction: the subject is a *task's* budget on every
 * one of the four screens, so this is where the words belong.
 */
import { t } from "$lib/core/i18n";
import { formatMinutes } from "$lib/modules/time/format";

export interface TaskBudgetFields {
  allocated_minutes?: number | null;
  logged_minutes?: number | null;
  remaining_minutes?: number | null;
}

export interface TaskBurn {
  /** Minutes booked against the task. */
  spent: number;
  /** The allocation, or `null` when the task has none — spend is still on the record. */
  budget: number | null;
  /** The API's own remainder, unclamped: over budget is negative. `null` without a budget. */
  remaining: number | null;
  /** "1u 30m / 3u", or just "1u 30m" when there is no allowance to compare against. */
  spentText: string;
  /** "1u 30m over" / "20m over budget". `undefined` when there is nothing to remain of. */
  remainingText: string | undefined;
}

/**
 * The task's burn, or `null` when there is nothing to draw — no logged minutes were returned
 * (the caller may not read hours, or nothing was asked for) or the task is untouched and
 * unbudgeted, in which case the plain allocation says everything there is to say.
 */
export function taskBurn(task: TaskBudgetFields): TaskBurn | null {
  const spent = task.logged_minutes;
  if (spent == null) return null;
  const budget = task.allocated_minutes ?? null;
  if (budget == null && spent === 0) return null;
  // The API computed the remainder with the allocation it stored; re-deriving it here would be
  // a second opinion about a question already answered.
  const remaining = budget == null ? null : (task.remaining_minutes ?? budget - spent);
  return {
    spent,
    budget,
    remaining,
    spentText:
      budget == null ? formatMinutes(spent) : `${formatMinutes(spent)} / ${formatMinutes(budget)}`,
    remainingText: remaining == null ? undefined : remainingText(remaining),
  };
}

/** "20m over" / "1u 30m over budget" — the one wording, so four surfaces cannot word it three ways. */
export function remainingText(remaining: number): string {
  return remaining < 0
    ? t("tasks.budget.over", { amount: formatMinutes(-remaining) })
    : t("tasks.budget.left", { amount: formatMinutes(remaining) });
}
