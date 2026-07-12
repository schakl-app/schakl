import { fmtClockTime } from "$lib/core/format";

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

/** ISO datetime → the user's clock preference (issue #13): "13:00", or "1:00 PM" on 12h.
 *  The UTC extraction keeps the stored wall-clock; `fmtClockTime` owns the 12/24h rendering —
 *  never bolt a meridiem onto the 24-hour digits. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return fmtClockTime(_timeFmt.format(new Date(iso)));
}

/** Minutes → decimal hours rounded to one place (e.g. 105 → 1.8). */
export function hoursFromMinutes(minutes: number): number {
  return Math.round((minutes / 60) * 10) / 10;
}

/** Where an entry sits in the sign-off chain. Read-only sugar over the three stored columns. */
export type EntryStatus = "open" | "approved" | "to_invoice" | "invoiced";

/**
 * The one place that turns `billable × approved_at × invoiced_at` into the word the UI prints.
 *
 * It mirrors the report's status filter exactly (`overview/+page.server.ts::statusFlags`), so a
 * row can never show a pill the filter that produced it disagrees with. Invoiced implies approved
 * — the API enforces that, and the order of these branches is what keeps the UI honest about it
 * (docs/UX.md: states never contradict each other).
 */
export function entryStatus(entry: {
  billable?: boolean;
  approved_at?: string | null;
  invoiced_at?: string | null;
}): EntryStatus {
  if (entry.invoiced_at) return "invoiced";
  if (entry.approved_at) return entry.billable ? "to_invoice" : "approved";
  return "open";
}
