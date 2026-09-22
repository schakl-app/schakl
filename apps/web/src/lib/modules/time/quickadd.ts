/**
 * Pure helpers for the AI quick-add flow on /time (#246).
 *
 * They live outside the component for one reason: both encode a rule that is easy to get
 * subtly wrong and impossible to see in a screenshot, so both are unit-tested.
 */
// Relative, extension and all: node's test runner loads this file directly (see `today.ts`).
import { wallClockIn } from "../../core/wallclock.ts";
import { getTimeZone } from "../../core/timezone.ts";

/**
 * Does a prefill built for `prefillDate` survive a navigation to `newDay`?
 *
 * The page wipes AI state when the selected day changes, which is right when the *user*
 * moves on and wrong when the prefill itself caused the move. A quick add now fills the form
 * before navigating to the parsed day (the navigation is a view change the form does not wait
 * for), so without this guard "gisteren 2 uur" prefills the form and then immediately blanks
 * it.
 */
export function shouldKeepPrefill(prefillDate: string | null, newDay: string): boolean {
  return prefillDate !== null && prefillDate === newDay;
}

/**
 * The end of the last entry on a day, as `HH:MM` on the **org's** clock, or null when the day is
 * empty. `zone` is the tenant's unless a test names one: reading `getHours()` off the `Date`
 * gave the *browser's* clock, which is the org's only on a desk in the same zone (§8).
 */
export function endOfDay(
  entries: readonly { started_at: string; ended_at?: string | null }[],
  zone: string = getTimeZone(),
): string | null {
  let latest: Date | null = null;
  for (const entry of entries) {
    const stamp = entry.ended_at ?? null;
    if (!stamp) continue; // a running timer has no end yet
    const at = new Date(stamp);
    if (Number.isNaN(at.getTime())) continue;
    if (latest === null || at > latest) latest = at;
  }
  if (latest === null) return null;
  return wallClockIn(latest.toISOString(), zone).time;
}

/**
 * Where a duration-only entry ("2 uur", "1,5 uur Jansen") should start.
 *
 * After the last thing you logged that day, because that is what "and then I did this for two
 * hours" means. A day with nothing on it falls back to `fallback` — the hardcoded 09:00 this
 * replaces put every afternoon's work in the morning.
 */
export function nextStartFrom(
  entries: readonly { started_at: string; ended_at?: string | null }[],
  fallback = "09:00",
  zone: string = getTimeZone(),
): string {
  return endOfDay(entries, zone) ?? fallback;
}
