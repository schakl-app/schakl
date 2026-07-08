/**
 * Leave helpers: tenant-defined type labels, hours↔days conversion (leave is tracked in
 * hours, CLAUDE.md §14), and workday math for suggested request hours.
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

/** Tenant-provided label in the UI locale (labels are tenant data, not message keys). */
export function typeLabel(type: LeaveTypeInfo | undefined, locale: string): string {
  if (!type) return "";
  return type.label_i18n[locale] ?? type.label_i18n.nl ?? type.label_i18n.en ?? type.key;
}

/** "16" / "16,5" — hours in the active locale, without unnecessary decimals. */
export function fmtHours(hours: number | string): string {
  return new Intl.NumberFormat(dateLocale(), { maximumFractionDigits: 2 }).format(Number(hours));
}

/** Hours expressed in workdays of the employee's contract (5-day week). */
export function hoursToDays(hours: number | string, hoursPerWeek: number | string): number {
  const perDay = Number(hoursPerWeek) / 5;
  return perDay > 0 ? Number(hours) / perDay : 0;
}

/** Mon–Fri days in the inclusive date-only ISO range. */
export function workdaysBetween(startIso: string, endIso: string): number {
  const start = new Date(startIso + "T00:00:00Z");
  const end = new Date(endIso + "T00:00:00Z");
  let count = 0;
  for (let d = new Date(start); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    const day = d.getUTCDay();
    if (day !== 0 && day !== 6) count += 1;
  }
  return count;
}

/** Suggested request hours: workdays in range × contract hours per day. */
export function suggestedHours(
  startIso: string,
  endIso: string,
  hoursPerWeek: number | string,
): number {
  if (!startIso || !endIso || endIso < startIso) return 0;
  return workdaysBetween(startIso, endIso) * (Number(hoursPerWeek) / 5);
}

/**
 * Spread a request's hours evenly over its workdays → per-ISO-date hours. Drives the
 * timesheet leave row (approved leave shows there without becoming a time entry, §14).
 */
export function leaveHoursByDay(item: {
  start_date: string;
  end_date: string;
  hours: number | string;
}): Map<string, number> {
  const days: string[] = [];
  const start = new Date(item.start_date + "T00:00:00Z");
  const end = new Date(item.end_date + "T00:00:00Z");
  for (let d = new Date(start); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    const day = d.getUTCDay();
    if (day !== 0 && day !== 6) days.push(d.toISOString().slice(0, 10));
  }
  const result = new Map<string, number>();
  if (days.length === 0) return result;
  const perDay = Number(item.hours) / days.length;
  for (const day of days) result.set(day, perDay);
  return result;
}
