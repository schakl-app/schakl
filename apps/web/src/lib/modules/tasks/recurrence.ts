/**
 * Reading a repeat rule back (#335).
 *
 * The rule was writable and unreadable: a chip said "↻ Maandelijks" and nothing anywhere said
 * *every how many*, *on which day*, *in which mode*, or *when the next one arrives*. One
 * function answers all four, so the chip under the title, the Planning card and the editor's
 * own preview line are the same sentence rather than three approximations of it.
 *
 * The plan is a list of *placed* blocks now — "de dinsdag ervoor om 09:00, de deadline zelf om
 * 14:00" — and `planBlocks` is the one reader of both the stored shapes, exactly as the API's
 * `plan_blocks` is on its side.
 */
import { monthNames, weekdayNames } from "$lib/core/format";
import { t } from "$lib/core/i18n";

export type RecurrenceFreq = "daily" | "weekly" | "monthly" | "quarterly" | "yearly";
export type RecurrenceMode = "after_completion" | "schedule";
export type Placement = "due" | "offset" | "weekday" | "day";

export const PLACEMENTS: Placement[] = ["due", "offset", "weekday", "day"];
/** Which week of the month an n-th weekday names; `-1` is the last one. */
export const WEEKS = [1, 2, 3, 4, -1] as const;

export interface PlanBlock {
  on: Placement;
  days?: number | null;
  weekday?: number | null;
  week?: number | null;
  day?: number | null;
  /** `null`/absent: the occurrence's own roster. */
  user_ids?: string[] | null;
  /** "HH:MM" or "HH:MM:SS" — the API stores a `time`, the form edits "HH:MM". */
  start_time: string;
  duration_minutes: number;
  note?: string | null;
}

export interface RecurrencePlan {
  user_id?: string | null;
  start_time?: string | null;
  duration_minutes?: number | null;
  blocks?: PlanBlock[] | null;
}

export interface Recurrence {
  freq: RecurrenceFreq;
  interval: number;
  mode: RecurrenceMode;
  on_weekday?: number | null;
  on_day?: number | null;
  on_month?: number | null;
  on_week?: number | null;
  plan?: RecurrencePlan | null;
}

export const FREQS: RecurrenceFreq[] = ["daily", "weekly", "monthly", "quarterly", "yearly"];

/** Which anchor control a frequency offers — the same table the API validates against. */
export function anchorKind(freq: RecurrenceFreq): "none" | "weekday" | "day" | "date" {
  if (freq === "weekly") return "weekday";
  if (freq === "monthly" || freq === "quarterly") return "day";
  if (freq === "yearly") return "date";
  return "none";
}

/**
 * "Elke maand" / "Elke 2 weken" — the cadence as one phrase, never a bare number in a box.
 *
 * Plurals are paired `_one`/`_other` keys picked here, not ICU: the Paraglide setup in this repo
 * does not parse `{n, plural, …}` and compiles it to garbage.
 */
export function cadenceLabel(rec: Pick<Recurrence, "freq" | "interval">): string {
  const key = `tasks.recurrence.unit.${UNIT[rec.freq]}`;
  return rec.interval === 1 ? t(`${key}_one`) : t(`${key}_other`, { count: String(rec.interval) });
}

const UNIT: Record<RecurrenceFreq, string> = {
  daily: "day",
  weekly: "week",
  monthly: "month",
  quarterly: "quarter",
  yearly: "year",
};

/** "de tweede" / "de laatste" — the ordinal an n-th weekday is named by. */
export function weekLabel(week: number): string {
  return t(`tasks.recurrence.week.${week === -1 ? "last" : String(week)}`);
}

/** "op dag 1" / "op maandag" / "op de tweede dinsdag" / "op 15 maart" — or "" when the rule
 *  follows the due date. */
export function anchorLabel(rec: Recurrence): string {
  const kind = anchorKind(rec.freq);
  if (kind === "weekday" && rec.on_weekday != null) {
    return t("tasks.recurrence.summary.on_weekday", { weekday: weekdayNames()[rec.on_weekday] });
  }
  if (kind !== "none" && rec.on_weekday != null && rec.on_week != null) {
    const nth = t("tasks.recurrence.summary.on_nth_weekday", {
      week: weekLabel(rec.on_week),
      weekday: weekdayNames()[rec.on_weekday],
    });
    return kind === "date" && rec.on_month != null
      ? t("tasks.recurrence.summary.of_month", { what: nth, month: monthNames()[rec.on_month - 1] })
      : nth;
  }
  if (kind === "day" && rec.on_day != null) {
    return t("tasks.recurrence.summary.on_day", { day: String(rec.on_day) });
  }
  if (kind === "date" && rec.on_day != null && rec.on_month != null) {
    return t("tasks.recurrence.summary.on_date", {
      day: String(rec.on_day),
      month: monthNames()[rec.on_month - 1],
    });
  }
  return "";
}

/**
 * The whole rule as one line: "Elke maand · op dag 1 · op schema".
 *
 * `compact` drops the mode — the chip under the title has a few characters to work with and the
 * mode is the part a reader can look up in the Planning card below it.
 */
export function recurrenceSentence(rec: Recurrence, options?: { compact?: boolean }): string {
  const parts = [cadenceLabel(rec)];
  const anchor = anchorLabel(rec);
  if (anchor) parts.push(anchor);
  if (!options?.compact) parts.push(t(`tasks.recurrence.mode.${rec.mode}`));
  return parts.join(" · ");
}

/** "09:00" from whatever shape the time arrived in ("09:00:00", "09:00"). */
export function clockOf(time: string | null | undefined): string {
  return (time ?? "").slice(0, 5);
}

/**
 * The plan as a list of placed blocks, whichever shape it was stored in — the legacy single
 * clock reads as one block on the due date, exactly as the API reads it.
 */
export function planBlocks(rec: Recurrence | null | undefined): PlanBlock[] {
  const plan = rec?.plan;
  if (!plan) return [];
  if (plan.blocks?.length) return plan.blocks.map((block) => ({ ...block }));
  if (!plan.start_time || !plan.duration_minutes) return [];
  return [
    {
      on: "due",
      user_ids: plan.user_id ? [plan.user_id] : null,
      start_time: plan.start_time,
      duration_minutes: plan.duration_minutes,
      note: null,
    },
  ];
}

/**
 * Where a block lands, in words: "op de deadline", "2 dagen ervoor", "op de dinsdag van die
 * week", "op de tweede dinsdag van de maand", "op dag 15 van de maand".
 */
export function placementLabel(
  block: Pick<PlanBlock, "on" | "days" | "weekday" | "week" | "day">,
): string {
  switch (block.on) {
    case "offset": {
      const days = block.days ?? 0;
      const count = String(Math.abs(days));
      if (days < 0) {
        return Math.abs(days) === 1
          ? t("tasks.recurrence.placement.before_one")
          : t("tasks.recurrence.placement.before_other", { count });
      }
      return days === 1
        ? t("tasks.recurrence.placement.after_one")
        : t("tasks.recurrence.placement.after_other", { count });
    }
    case "weekday": {
      const weekday = weekdayNames()[block.weekday ?? 0];
      return block.week == null
        ? t("tasks.recurrence.placement.weekday_in_week", { weekday })
        : t("tasks.recurrence.placement.nth_weekday", { week: weekLabel(block.week), weekday });
    }
    case "day":
      return t("tasks.recurrence.placement.day_of_month", { day: String(block.day ?? 1) });
    default:
      return t("tasks.recurrence.placement.due");
  }
}

/** "3 blokken per herhaling" / "1 blok per herhaling, om 09:00" — the Planning card's one line. */
export function planSummary(rec: Recurrence | null | undefined): string {
  const blocks = planBlocks(rec);
  if (blocks.length === 0) return "";
  if (blocks.length === 1) {
    return t("tasks.recurrence.plan.summary_one", {
      placement: placementLabel(blocks[0]),
      time: clockOf(blocks[0].start_time),
    });
  }
  return t("tasks.recurrence.plan.summary_other", { count: String(blocks.length) });
}
