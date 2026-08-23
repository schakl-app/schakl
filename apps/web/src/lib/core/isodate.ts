/**
 * Day math on a date-only ISO string (`YYYY-MM-DD`) — pure, UTC, and importing nothing.
 *
 * This is the half of `calendar.ts` that has no runtime around it. It was lifted out for the
 * same reason `core/state.ts` keeps its glyphs elsewhere and `today.ts` splits `todayIn` from
 * `orgToday`: node's test runner loads a `.ts` file directly, so a module that reaches i18n (and
 * through it the generated paraglide bundle) is a module that cannot be unit-tested. `due.ts`
 * needs exactly these three functions and nothing else in `calendar.ts`, and "which bucket is
 * this deadline in" is precisely the kind of rule that has to be assertable.
 *
 * `calendar.ts` re-exports them, so every existing `$lib/core/calendar` import keeps working and
 * there is still one copy of the arithmetic.
 *
 * **UTC on purpose.** These take a wall-clock date and hand one back; there is no clock in them
 * to get wrong, which is what makes them legal under `scripts/today-check.mjs`. The zone question
 * is answered once, upstream, by `orgToday()` — the value passed *in*.
 */

export function isoAddDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Whole days from `from` to `to` (both date-only ISO); negative when `to` lies earlier. */
export function isoDiffDays(from: string, to: string): number {
  return Math.round(
    (new Date(to + "T00:00:00Z").getTime() - new Date(from + "T00:00:00Z").getTime()) / 86400000,
  );
}

/** ISO Monday on or before the given date. */
export function mondayOnOrBefore(isoDate: string): string {
  const d = new Date(isoDate + "T00:00:00Z");
  return isoAddDays(isoDate, -((d.getUTCDay() + 6) % 7));
}
