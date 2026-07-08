/**
 * Date-only grid math for the shared calendar (`/calendar`). Pure ISO-string helpers (UTC,
 * like `core/format.ts`) shared by the page load (fetch range) and the month grid component.
 */

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
