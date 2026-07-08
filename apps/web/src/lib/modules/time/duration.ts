/**
 * Pure duration helpers for the time-entry form: live worked-minutes calculation and a
 * forgiving duration parser ("1:30", "90m", "1,5", "2h 15m", "90" → minutes).
 * All math is on wall-clock HH:MM strings and integer minutes — timezone-free by design.
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

/**
 * Forgiving duration parser → minutes, or null when unreadable.
 * Accepts "1:30", "2h", "2h15", "2h 15m", "90m", "90", "1,5" and "1.5" (decimal hours).
 */
export function parseDurationText(raw: string): number | null {
  const text = raw.trim().toLowerCase().replace(",", ".");
  if (!text) return null;

  const colon = /^(\d{1,2}):(\d{2})$/.exec(text);
  if (colon) return Number(colon[1]) * 60 + Number(colon[2]);

  const hoursMinutes = /^(\d{1,2})\s*h(?:\s*(\d{1,2})\s*m?)?$/.exec(text);
  if (hoursMinutes) return Number(hoursMinutes[1]) * 60 + Number(hoursMinutes[2] ?? 0);

  const minutesOnly = /^(\d{1,4})\s*m$/.exec(text);
  if (minutesOnly) return Number(minutesOnly[1]);

  const decimal = /^(\d{1,2}(?:\.\d+)?)$/.exec(text);
  if (decimal) {
    const value = Number(decimal[1]);
    // A bare integer ≥ 5 reads as minutes ("90" → 90m); small/decimal values as hours.
    if (Number.isInteger(value) && value >= 5) return value;
    return Math.round(value * 60);
  }
  return null;
}

/** Minutes → the canonical text shown in the duration input ("1:30"). */
export function formatDurationInput(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}
