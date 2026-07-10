/**
 * Work-schedule shapes and the minute arithmetic the editor previews with (#46).
 *
 * The **server** is the authority on hours (see `LeaveService.compute_hours`); this exists so
 * the weekly grid can print "= 40 u/week" while you drag a break around, without a round-trip
 * per keystroke. It mirrors `apps/api/app/modules/leave/schedule.py` exactly — same clamp,
 * same break subtraction, same order of operations. If the two ever disagree, the API wins.
 */

/** Monday first (ISO). `Date.getUTCDay()` needs `(day + 6) % 7` to index this. */
export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

export interface BreakWindow {
  start: string; // "HH:MM"
  end: string;
}

export interface WorkDay {
  start: string;
  end: string;
  breaks: BreakWindow[];
}

export type WorkSchedule = Record<Weekday, WorkDay | null>;

/** The shipped default: 08:30–17:00 with a 12:30–13:00 lunch → 8.0 h/day, 40 h/week. */
export function defaultWorkDay(): WorkDay {
  return { start: "08:30", end: "17:00", breaks: [{ start: "12:30", end: "13:00" }] };
}

export function defaultSchedule(): WorkSchedule {
  return {
    mon: defaultWorkDay(),
    tue: defaultWorkDay(),
    wed: defaultWorkDay(),
    thu: defaultWorkDay(),
    fri: defaultWorkDay(),
    sat: null,
    sun: null,
  };
}

/** A deep copy — the editor mutates days and breaks in place. */
export function cloneSchedule(schedule: WorkSchedule): WorkSchedule {
  return Object.fromEntries(
    WEEKDAYS.map((day) => {
      const value = schedule[day];
      return [day, value ? { ...value, breaks: value.breaks.map((b) => ({ ...b })) } : null];
    }),
  ) as WorkSchedule;
}

export function toMinutes(hhmm: string): number {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  return match ? Number(match[1]) * 60 + Number(match[2]) : 0;
}

function overlap(aStart: number, aEnd: number, bStart: number, bEnd: number): number {
  return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));
}

/** Worked minutes of `day`, breaks removed. Mirrors `schedule.day_minutes`. */
export function dayMinutes(day: WorkDay | null): number {
  if (!day) return 0;
  const start = toMinutes(day.start);
  const end = toMinutes(day.end);
  if (end <= start) return 0;
  let worked = end - start;
  for (const window of day.breaks) {
    worked -= overlap(start, end, toMinutes(window.start), toMinutes(window.end));
  }
  return Math.max(0, worked);
}

export function dayHours(day: WorkDay | null): number {
  return Math.round((dayMinutes(day) / 60) * 100) / 100;
}

export function weekHours(schedule: WorkSchedule): number {
  const minutes = WEEKDAYS.reduce((total, day) => total + dayMinutes(schedule[day]), 0);
  return Math.round((minutes / 60) * 100) / 100;
}

/**
 * What the day is wrong about, as an i18n key — or `null` when it's fine. Same rules the API
 * enforces (`WorkDay._check`), so the grid can say so before you press save.
 */
export function dayError(day: WorkDay | null): string | null {
  if (!day) return null;
  const start = toMinutes(day.start);
  const end = toMinutes(day.end);
  if (start >= end) return "errors.leave_schedule_day_invalid";

  const sorted = [...day.breaks].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  let previousEnd = start;
  let consumed = 0;
  for (const window of sorted) {
    const from = toMinutes(window.start);
    const to = toMinutes(window.end);
    if (from >= to) return "errors.leave_schedule_break_invalid";
    if (from < start || to > end) return "errors.leave_schedule_break_outside";
    if (from < previousEnd) return "errors.leave_schedule_breaks_overlap";
    previousEnd = to;
    consumed += to - from;
  }
  if (consumed >= end - start) return "errors.leave_schedule_day_empty";
  return null;
}

/** The first thing wrong with the whole week, as an i18n key. */
export function scheduleError(schedule: WorkSchedule): string | null {
  if (WEEKDAYS.every((day) => schedule[day] === null)) return "errors.leave_schedule_empty";
  for (const day of WEEKDAYS) {
    const error = dayError(schedule[day]);
    if (error) return error;
  }
  return null;
}
