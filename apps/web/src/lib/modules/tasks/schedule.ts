/**
 * Task-scheduling helpers shared by the calendar source, the schedule modal and the task
 * panel (#188). The API stores/returns UTC instants; the browser only ever converts *from* an
 * instant *to* the org-local day/time (deterministic and DST-safe), never the other way — that
 * direction is the API's job, so a day-drag stays correct across a DST boundary (§8).
 */
import { getTimeZone } from "$lib/core/timezone";

/** An instant → its org-local calendar day (`yyyy-mm-dd`) and 24-hour clock time (`HH:MM`). */
export function localDayTime(iso: string): { day: string; time: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: getTimeZone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const g = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return { day: `${g("year")}-${g("month")}-${g("day")}`, time: `${g("hour")}:${g("minute")}` };
}

/** Whole worked minutes between two instants — the block's length, for the log-time prefill. */
export function durationMinutes(startsAt: string, endsAt: string): number {
  return Math.round((new Date(endsAt).getTime() - new Date(startsAt).getTime()) / 60_000);
}
