/**
 * A date formatter handed a timestamp printed `NaN-NaN-0NaN`.
 *
 * The API sends two shapes down the same `string` type — a wall-clock date (`2026-07-07`) and an
 * instant (`2026-07-07T09:12:33Z`) — and `format.ts` assumed the first one everywhere: it parsed
 * by concatenating a midnight, so an instant became `…T09:12:33ZT00:00:00Z`, an Invalid Date, and
 * `String(NaN).padStart(4, "0")` for the year. It reached five screens at once because there is
 * nothing for the build to catch: the types agree, `svelte-check` passes, and the garbage only
 * shows on a row that actually carries a timestamp.
 *
 * So the shape is now read off the value, and this is where that read is pinned. The first test
 * is the reported bug and would have failed before the fix; the rest are the two properties that
 * make the discrimination safe — a date-only value must not move (the reason UTC was pinned in
 * the first place), and an instant must resolve in the *tenant's* zone, not the runner's.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  isInstant,
  numericDate,
  parseWireDate,
  wireDateParts,
  wireZone,
} from "../../src/lib/core/wire-date.ts";

const AMS = "Europe/Amsterdam";

describe("the two wire shapes", () => {
  test("an instant no longer formats as NaN", () => {
    // The exact value the Google Ads panels put behind "gecontroleerd".
    assert.equal(numericDate("2026-08-15T09:12:33.481920Z", AMS, "dd-mm-yyyy"), "15-08-2026");
    assert.equal(numericDate("2026-08-15T09:12:33Z", AMS, "dd-mm-yyyy"), "15-08-2026");
    assert.equal(numericDate("2026-08-15T09:12:33+02:00", AMS, "dd-mm-yyyy"), "15-08-2026");
  });

  test("a `T` is what tells the two apart", () => {
    assert.equal(isInstant("2026-08-15"), false);
    assert.equal(isInstant("2026-08-15T09:12:33Z"), true);
  });

  test("a date-only value is still read in UTC, so it never slips a day", () => {
    // Pinned in UTC on purpose: a due date has no zone, and reading it in one moves it.
    assert.equal(wireZone("2026-08-15", AMS), "UTC");
    assert.equal(parseWireDate("2026-08-15").toISOString(), "2026-08-15T00:00:00.000Z");
    for (const tenantZone of [AMS, "Pacific/Auckland", "America/Los_Angeles", "UTC"]) {
      assert.equal(numericDate("2026-08-15", tenantZone, "dd-mm-yyyy"), "15-08-2026");
    }
  });

  test("an instant is read in the tenant's zone, not the viewer's or UTC", () => {
    assert.equal(wireZone("2026-08-15T09:12:33Z", AMS), AMS);
    // 22:30 UTC is already the 16th in Amsterdam and still the 15th in Los Angeles. Whichever
    // machine renders this, the tenant's calendar is the one that answers.
    const late = "2026-08-15T22:30:00Z";
    assert.equal(numericDate(late, AMS, "dd-mm-yyyy"), "16-08-2026");
    assert.equal(numericDate(late, "America/Los_Angeles", "dd-mm-yyyy"), "15-08-2026");
  });

  test("the parts are zero-padded digits whatever the order", () => {
    assert.deepEqual(wireDateParts("2026-07-07T09:12:33Z", AMS), {
      day: "07",
      month: "07",
      year: "2026",
    });
    assert.equal(numericDate("2026-07-07T09:12:33Z", AMS, "yyyy-mm-dd"), "2026-07-07");
    assert.equal(numericDate("2026-07-07T09:12:33Z", AMS, "mm-dd-yyyy"), "07-07-2026");
    assert.equal(numericDate("2026-07-07", AMS, "dd-mm-yyyy"), "07-07-2026");
  });
});
