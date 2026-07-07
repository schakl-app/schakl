/** Format a duration in minutes as "Hh Mm" (e.g. 135 → "2h 15m", 40 → "40m"). */
export function formatMinutes(total: number): string {
  const mins = Math.max(0, Math.round(total));
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

// Entry times are stored as the wall-clock the user typed (as UTC), so render them in UTC to
// round-trip exactly — the whole app is single-timezone (Europe/Amsterdam) in practice.
const _timeFmt = new Intl.DateTimeFormat("nl-NL", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

/** ISO datetime → "HH:MM" (e.g. "2026-07-07T09:30:00Z" → "09:30"). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return _timeFmt.format(new Date(iso));
}

/** Minutes → decimal hours rounded to one place (e.g. 105 → 1.8). */
export function hoursFromMinutes(minutes: number): number {
  return Math.round((minutes / 60) * 10) / 10;
}
