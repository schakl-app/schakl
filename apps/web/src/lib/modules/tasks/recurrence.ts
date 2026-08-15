/**
 * Reading a repeat rule back (#335).
 *
 * The rule was writable and unreadable: a chip said "↻ Maandelijks" and nothing anywhere said
 * *every how many*, *on which day*, *in which mode*, or *when the next one arrives*. One
 * function answers all four, so the chip under the title, the Planning card and the editor's
 * own preview line are the same sentence rather than three approximations of it.
 */
import { monthNames, weekdayNames } from "$lib/core/format";
import { t } from "$lib/core/i18n";

export type RecurrenceFreq = "daily" | "weekly" | "monthly" | "quarterly" | "yearly";
export type RecurrenceMode = "after_completion" | "schedule";

export interface RecurrencePlan {
  user_id?: string | null;
  /** "HH:MM" or "HH:MM:SS" — the API stores a `time`, the form edits "HH:MM". */
  start_time: string;
  duration_minutes: number;
}

export interface Recurrence {
  freq: RecurrenceFreq;
  interval: number;
  mode: RecurrenceMode;
  on_weekday?: number | null;
  on_day?: number | null;
  on_month?: number | null;
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

/** "op dag 1" / "op maandag" / "op 15 maart" — or "" when the rule follows the due date. */
export function anchorLabel(rec: Recurrence): string {
  const kind = anchorKind(rec.freq);
  if (kind === "weekday" && rec.on_weekday != null) {
    return t("tasks.recurrence.summary.on_weekday", { weekday: weekdayNames()[rec.on_weekday] });
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
