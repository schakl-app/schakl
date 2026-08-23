/**
 * What day it is *where the tenant is* — the browser's and the server's answer to "vandaag".
 *
 * CLAUDE.md §8 already states this rule for the API ("no module keeps its own clock", fixed there
 * with `org_today` / `org_zoneinfo`). The web app was never brought along, and the identical bug
 * was sitting in twenty call sites reading `new Date().toISOString().slice(0, 10)` — which is not
 * the org's clock, and not even the viewer's: it is **UTC's**.
 *
 * In `Europe/Amsterdam` the UTC date rolls over at 02:00 local, so between midnight and 02:00
 * every one of them named *yesterday*: a task due today compared as `> today` and was filed under
 * *Binnenkort*, and a task due yesterday compared as `== today` and was drawn as *Vandaag* in
 * black rather than overdue in red. Two hours a night, in the exact colour the urgency work is
 * about. The two `+page.server.ts` cases were worse in kind — they read the Node process's clock,
 * which is UTC in the shipped image, so they were a day out *all day* for every tenant east of it.
 *
 * The zone comes from the same resolver every date on screen is formatted with
 * (`getTimeZone()`: AsyncLocalStorage during SSR, `<html data-timezone>` in the browser), so a
 * date this produces and a date `format.ts` prints can never disagree.
 *
 * `en-CA` is chosen because it formats as `YYYY-MM-DD` — the API's own wire shape — which avoids
 * both hand-assembling the string from `formatToParts` and the off-by-one that `toISOString()` on
 * a locally-constructed `Date` produces.
 *
 * `scripts/today-check.mjs` fails a new `toISOString().slice(0, 10)` on a clock read, because this
 * is the bug class that is invisible in review and wrong only on a clock nobody is watching.
 */
// Relative, extension and all — `settings-nav.ts` does the same, and for the same reason: node's
// test runner loads this file directly, and it knows neither the `$lib` alias nor extensionless
// ESM resolution. `timezone.ts` imports nothing itself, so this stays cheap in every bundle.
import { getTimeZone } from "./timezone.ts";

// One formatter per zone: `Intl.DateTimeFormat` construction is the expensive half, and these are
// called per render. Mirrors the caches in `wire-date.ts` and the two `format.ts` day formatters.
const _fmt = new Map<string, Intl.DateTimeFormat>();

function formatterFor(zone: string): Intl.DateTimeFormat {
  let formatter = _fmt.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    _fmt.set(zone, formatter);
  }
  return formatter;
}

/**
 * The calendar date `now` falls on in `zone`, as `YYYY-MM-DD`.
 *
 * The pure half, taking both of its inputs — the shape `wire-date.ts` uses for the same reason:
 * a boundary this narrow is only pinnable by a test that can name the instant *and* the zone.
 */
export function todayIn(zone: string, now: Date = new Date()): string {
  return formatterFor(zone).format(now);
}

/** Today's calendar date in the tenant's zone, as `YYYY-MM-DD`. The one every caller wants. */
export function orgToday(now: Date = new Date()): string {
  return todayIn(getTimeZone(), now);
}

/**
 * The calendar year it is in the tenant's zone.
 *
 * The same fault one unit up, and it was in eight places: a leave balance, an entitlement read and
 * the revenue report all asked `new Date().getUTCFullYear()`, so on 1 January between midnight and
 * 02:00 — and, in the server loads, for the whole of New Year's Day in any tenant ahead of UTC —
 * they opened on the year that had just ended.
 */
export function orgYear(now: Date = new Date()): number {
  return Number(orgToday(now).slice(0, 4));
}
