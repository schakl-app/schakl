/**
 * Date-only grid math for the shared calendar (`/calendar`). Pure ISO-string helpers (UTC,
 * like `core/format.ts`) shared by the page load (fetch range) and the view components.
 */
import type { CalendarEvent } from "./registry";

export const CALENDAR_VIEWS = ["day", "week", "month", "year"] as const;
export type CalendarView = (typeof CALENDAR_VIEWS)[number];

export function isCalendarView(value: string | null | undefined): value is CalendarView {
  return !!value && (CALENDAR_VIEWS as readonly string[]).includes(value);
}

export function isoAddDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** "2026-07" for an ISO date. */
export function monthOf(isoDate: string): string {
  return isoDate.slice(0, 7);
}

/** Shift a "yyyy-mm" month by n months. */
export function addMonths(month: string, n: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

/** ISO Monday on or before the given date. */
export function mondayOnOrBefore(isoDate: string): string {
  const d = new Date(isoDate + "T00:00:00Z");
  return isoAddDays(isoDate, -((d.getUTCDay() + 6) % 7));
}

/**
 * The Monday-start grid of full weeks covering a "yyyy-mm" month: 35 or 42 date-only ISO
 * strings. First cell = the grid fetch range start, last cell = its end.
 */
export function monthGrid(month: string): string[] {
  const first = `${month}-01`;
  const start = mondayOnOrBefore(first);
  const lastDay = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0))
    .toISOString()
    .slice(0, 10);
  const days: string[] = [];
  for (let d = start; ; d = isoAddDays(d, 1)) {
    days.push(d);
    if (d >= lastDay && new Date(d + "T00:00:00Z").getUTCDay() === 0) break;
  }
  return days;
}

/** The Monday-start week (7 date-only ISO strings) containing the given date. */
export function weekGrid(isoDate: string): string[] {
  const start = mondayOnOrBefore(isoDate);
  return Array.from({ length: 7 }, (_, i) => isoAddDays(start, i));
}

/** Fetch/display range for a view anchored on `date`: day/week/month grid bounds, or the
 *  full calendar year. */
export function rangeFor(view: CalendarView, date: string): { from: string; to: string } {
  if (view === "day") return { from: date, to: date };
  if (view === "week") {
    const days = weekGrid(date);
    return { from: days[0], to: days[days.length - 1] };
  }
  if (view === "month") {
    const days = monthGrid(monthOf(date));
    return { from: days[0], to: days[days.length - 1] };
  }
  const year = date.slice(0, 4);
  return { from: `${year}-01-01`, to: `${year}-12-31` };
}

/**
 * Shift `date` by `delta` units of `view`: day/week shift by days; month/year shift by
 * months, clamping the day-of-month to the target month's length (e.g. 31 Jan − 1 month →
 * 28/29 Feb).
 */
export function shiftDate(date: string, view: CalendarView, delta: number): string {
  if (view === "day") return isoAddDays(date, delta);
  if (view === "week") return isoAddDays(date, delta * 7);
  const [y, m, d] = date.split("-").map(Number);
  const monthsDelta = view === "year" ? delta * 12 : delta;
  const target = new Date(Date.UTC(y, m - 1 + monthsDelta, 1));
  const lastDay = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
  ).getUTCDate();
  target.setUTCDate(Math.min(d, lastDay));
  return target.toISOString().slice(0, 10);
}

/** Buckets events by every date-only ISO day they touch within `days` (multi-day events
 *  repeat per day, as the month grid already relies on). */
export function eventsByDayMap(
  days: string[],
  events: CalendarEvent[],
): Record<string, CalendarEvent[]> {
  const byDay: Record<string, CalendarEvent[]> = {};
  for (const day of days) {
    const hits = events.filter((e) => e.start <= day && e.end >= day);
    if (hits.length) byDay[day] = hits;
  }
  return byDay;
}

export interface CalendarDayAggregate {
  count: number;
  /** True only if every event touching this day is tentative (pending). */
  tentativeOnly: boolean;
}

/**
 * Per-day event counts, keyed by ISO date. Used by the year view so only aggregates — never
 * full event bodies — are sent to the client (docs/PERFORMANCE.md).
 */
export function aggregateEventsByDay(
  events: CalendarEvent[],
): Record<string, CalendarDayAggregate> {
  const byDay: Record<string, { count: number; allTentative: boolean }> = {};
  for (const event of events) {
    for (let d = event.start; d <= event.end; d = isoAddDays(d, 1)) {
      const entry = byDay[d] ?? { count: 0, allTentative: true };
      entry.count += 1;
      entry.allTentative = entry.allTentative && Boolean(event.tentative);
      byDay[d] = entry;
    }
  }
  const result: Record<string, CalendarDayAggregate> = {};
  for (const [day, { count, allTentative }] of Object.entries(byDay)) {
    result[day] = { count, tentativeOnly: allTentative };
  }
  return result;
}
