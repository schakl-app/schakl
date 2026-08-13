/**
 * Pure clock helpers for the time-entry form: wall-clock arithmetic on "HH:MM" strings and
 * integer minutes — timezone-free by design. The duration *parser* moved to `$lib/core/duration`
 * the moment a second module needed it (#326); import it from there.
 */

const HHMM = /^(\d{1,2}):(\d{2})$/;

/** "HH:MM" → minutes since midnight, or null when malformed. */
export function timeToMinutes(time: string): number | null {
  const m = HHMM.exec(time.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/** Minutes since midnight → "HH:MM" (wraps past midnight). */
export function minutesToTime(total: number): string {
  const wrapped = ((Math.round(total) % 1440) + 1440) % 1440;
  const h = Math.floor(wrapped / 60);
  const m = wrapped % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Wall-clock span from start to end; an end at or before the start rolls to the next day. */
export function minutesBetween(start: string, end: string): number | null {
  const s = timeToMinutes(start);
  const e = timeToMinutes(end);
  if (s == null || e == null) return null;
  return e > s ? e - s : e + 1440 - s;
}

/** End time implied by a start, worked minutes and a break. */
export function endFromDuration(start: string, worked: number, breakMinutes = 0): string | null {
  const s = timeToMinutes(start);
  if (s == null || worked <= 0) return null;
  return minutesToTime(s + worked + Math.max(0, breakMinutes));
}
