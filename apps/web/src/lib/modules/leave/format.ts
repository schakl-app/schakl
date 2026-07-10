/**
 * Leave helpers: tenant-defined type labels and the hours↔days conversion (leave is tracked in
 * hours, CLAUDE.md §14).
 *
 * The hour *arithmetic* is not here. It lives in `LeaveService.compute_hours` (#48), the only
 * place that knows the employee's schedule and the holiday calendar. The browser used to guess
 * with `suggestedHours()` / `workdaysBetween()` and to spread a request evenly with
 * `leaveHoursByDay()`; all three are gone. Two implementations of one rule is one too many, and
 * the browser's was the wrong one.
 */
import { dateLocale } from "$lib/core/format";

export interface LeaveTypeInfo {
  id: string;
  key: string;
  label_i18n: Record<string, string>;
  color: string;
  paid: boolean;
  tracks_balance: boolean;
  requires_approval: boolean;
  default_weeks: string | null;
  carry_over_months: number | null;
  position: number;
  active: boolean;
}

export interface HolidayInfo {
  id: string;
  date: string;
  name_i18n: Record<string, string>;
  active: boolean;
  source: string;
  key: string | null;
}

/** Tenant-provided label in the UI locale (labels are tenant data, not message keys). */
export function typeLabel(type: LeaveTypeInfo | undefined, locale: string): string {
  if (!type) return "";
  return type.label_i18n[locale] ?? type.label_i18n.nl ?? type.label_i18n.en ?? type.key;
}

/** Same rule for a holiday's name — tenant data, in whatever locales the tenant filled in. */
export function holidayName(names: Record<string, string>, locale: string): string {
  return names[locale] ?? names.nl ?? names.en ?? Object.values(names)[0] ?? "";
}

/** "16" / "16,5" — hours in the active locale, without unnecessary decimals. */
export function fmtHours(hours: number | string): string {
  return new Intl.NumberFormat(dateLocale(), { maximumFractionDigits: 2 }).format(Number(hours));
}

/**
 * Hours expressed in days of the employee's **average scheduled working day** (#46).
 *
 * Not `hoursPerWeek / 5`: a three-day week is still made of 8-hour days, and dividing a
 * part-timer's 24 h week by five tells them their working day is 4,8 hours long. The API
 * computes `hours_per_day` from the schedule and hands it over; the browser never guesses it.
 */
export function hoursToDays(hours: number | string, hoursPerDay: number | string): number {
  const perDay = Number(hoursPerDay);
  return perDay > 0 ? Number(hours) / perDay : 0;
}

/** One day of a request, as the API breaks it down (`POST /leave/requests/preview`). */
export interface LeaveDayHours {
  date: string;
  hours: string | number;
  /** `holiday` | `not_scheduled` | `outside_hours`, or null on an ordinary day. */
  reason: string | null;
}

/** Why a day of a request is worth nothing — as an i18n key, or null if it isn't. */
export function dayReasonKey(reason: string | null): string | null {
  if (!reason) return null;
  return `leave.reason.${reason}`;
}
