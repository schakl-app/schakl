/**
 * An hours-against-budget figure, worded once for every surface that draws one (#340).
 *
 * `0 / 5 u` and `5 / 5 u` are the same nine glyphs and were the same project: My Day printed
 * **spent** of budget, the companies and projects lists printed **remaining** of the same budget,
 * and both drew the identical bar underneath — so on the list an empty bar sat beside the figure
 * `5`, which reads as "5 used". A project four hours into a five-hour budget quoted `4 / 5 u` on
 * one screen and `1 / 5 u` on the other, which is the number an account manager repeats to a
 * client.
 *
 * One meaning, chosen because two things already agreed on it: **a bare `x / y u` is always
 * spent-of-budget** — what the bar has always drawn, and what `/time` already printed. A
 * **remainder never travels bare**; it carries its own word (`5 u over`, `1,5 u over budget`),
 * exactly as a task's minutes do (`modules/tasks/budget.ts`, #313). This is that file's
 * counterpart for the unit a project budgets in, and it exists for the same reason: a shared
 * component only fixes half of it while four callers still write the sentence themselves.
 *
 * `title` is what belongs on hover — the remainder in words, the period it counts over, and
 * whatever the arithmetic deliberately left out. `HoursCell` used to lead that tooltip with
 * `{spent} van {budget} u` while the cell beside it showed the remainder, so the element
 * disagreed with itself.
 */
import { fmtNumber } from "$lib/core/format";
import { t } from "$lib/core/i18n";

/** The API's budget-burn shape (`BudgetHours` / `CompanyBudgetHours`), as far as this reads it. */
export interface HoursFields {
  period?: string | null;
  budget_hours?: number | null;
  spent_hours?: number;
  unapproved_hours?: number;
  unbudgeted_hours?: number;
  remaining_hours?: number | null;
}

export interface HoursBurn {
  /** Hours booked. Drives the colour, never the text. */
  spent: number;
  /** The allowance, or `null` when there is none — the spend is still on the record. */
  budget: number | null;
  /** The API's own remainder, unclamped: over budget reads negative. `null` without a budget. */
  remaining: number | null;
  /** "0 / 5 u", or "3 u" when there is no allowance to compare against. */
  spentText: string;
  /** "5 u over" / "1,5 u over budget". `undefined` when there is nothing to remain of. */
  remainingText: string | undefined;
  /** What the bar deliberately does not account for, said out loud. */
  caveats: string[];
  /** Hover text: the remainder, the period, the caveats. Never a second reading of the figure. */
  title: string;
}

/** "0 / 5 u" — spent of budget, the only thing a bare `x / y` ever means here. */
export function hoursSpentText(spent: number, budget: number | null | undefined): string {
  return budget == null
    ? t("hours.spent", { hours: fmtNumber(spent) })
    : t("hours.of_budget", { spent: fmtNumber(spent), budget: fmtNumber(budget) });
}

/** "5 u over" / "1,5 u over budget" — the one wording, so five surfaces cannot word it three ways. */
export function hoursRemainingText(remaining: number): string {
  return remaining < 0
    ? t("hours.over", { hours: fmtNumber(-remaining) })
    : t("hours.left", { hours: fmtNumber(remaining) });
}

/**
 * The burn behind one budget block, or `null` when the caller was handed no figures at all —
 * which is not the same as a zero, and is drawn as an em-dash rather than a fabricated total.
 */
export function hoursBurn(hours: HoursFields | null | undefined): HoursBurn | null {
  if (!hours) return null;
  const spent = hours.spent_hours ?? 0;
  const budget = hours.budget_hours ?? null;
  // The API computed the remainder over the period it resolved on the org's own clock; deriving
  // it here would be a second opinion about a question already answered.
  const remaining =
    budget == null ? null : (hours.remaining_hours ?? Math.round((budget - spent) * 100) / 100);
  const caveats = [
    hours.unapproved_hours
      ? t("table.hours.unapproved", { hours: fmtNumber(hours.unapproved_hours) })
      : null,
    hours.unbudgeted_hours
      ? t("table.hours.unbudgeted", { hours: fmtNumber(hours.unbudgeted_hours) })
      : null,
  ].filter(Boolean) as string[];
  const periodText =
    budget == null
      ? null
      : hours.period
        ? t(`table.hours.period.${hours.period}`)
        : t("table.hours.period.mixed");
  return {
    spent,
    budget,
    remaining,
    spentText: hoursSpentText(spent, budget),
    remainingText: remaining == null ? undefined : hoursRemainingText(remaining),
    caveats,
    title: [
      remaining == null ? t("table.hours.no_budget") : hoursRemainingText(remaining),
      periodText,
      ...caveats,
    ]
      .filter(Boolean)
      .join(" · "),
  };
}
