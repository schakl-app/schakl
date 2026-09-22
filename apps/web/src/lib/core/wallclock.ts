/**
 * An instant → the org-local calendar day and clock it falls on (CLAUDE.md §8).
 *
 * The API stores and returns UTC instants; a screen that edits or groups by *day* needs the
 * wall clock those instants read as in the tenant's zone. The browser only ever converts *from*
 * an instant *to* a day/time — never the other way: a typed clock goes back to the API naive
 * (`2026-09-19T12:40:00`, no offset), and the API reads it in the org's zone, so a DST boundary
 * is the API's problem exactly once and never a second opinion in the browser.
 *
 * This used to live in `modules/tasks/schedule.ts` alone, and the time module grew its own
 * answer instead of borrowing it: `started_at.slice(11, 16)`, which is the UTC clock, not the
 * org's — so a timer started at 12:40 in Amsterdam listed as "10:40", and an agent that sent
 * `12:40:00+02:00` got the same. One helper now, in core, and the same `getTimeZone()` every
 * date on screen is formatted with (`today.ts`, `format.ts`), so a day this computes and a day
 * the page prints cannot disagree.
 *
 * `en-CA` with `h23` yields `YYYY-MM-DD` and `HH:MM` directly — the API's own wire shapes.
 */
// Relative, extension and all — `today.ts` does the same, and for the same reason: node's test
// runner loads this file directly and knows neither the `$lib` alias nor extensionless ESM.
import { getTimeZone } from "./timezone.ts";

// One formatter per zone: construction is the expensive half and this runs per row.
const _fmt = new Map<string, Intl.DateTimeFormat>();

function formatterFor(zone: string): Intl.DateTimeFormat {
  let formatter = _fmt.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    _fmt.set(zone, formatter);
  }
  return formatter;
}

/** The pure half: an instant's `{ day, time }` in `zone` (`yyyy-mm-dd`, `HH:MM`). */
export function wallClockIn(iso: string, zone: string): { day: string; time: string } {
  const parts = formatterFor(zone).formatToParts(new Date(iso));
  const g = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return { day: `${g("year")}-${g("month")}-${g("day")}`, time: `${g("hour")}:${g("minute")}` };
}

/** An instant's org-local calendar day and 24-hour clock — the one every screen wants. */
export function localDayTime(iso: string): { day: string; time: string } {
  return wallClockIn(iso, getTimeZone());
}
