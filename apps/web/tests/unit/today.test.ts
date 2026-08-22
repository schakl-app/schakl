/**
 * Which day the app thinks it is (`$lib/core/today.ts`).
 *
 * `new Date().toISOString().slice(0, 10)` is UTC's calendar date, and it was what twenty places
 * in the web app called "vandaag" (#396) — including `TaskRow`, so every board, panel and widget
 * in the product. In `Europe/Amsterdam` the UTC date rolls over at 02:00 local, so for two hours
 * a night a task due today compared as `> today` and was filed under *Binnenkort*, while a task
 * due yesterday compared as `== today` and was drawn in black instead of overdue red.
 *
 * Nothing in the build could see it: `YYYY-MM-DD` is a well-formed string whichever clock made
 * it, the types agree, and a developer looking at the screen in the afternoon sees the right
 * answer. So the boundary is pinned here, in both directions and from both sides of UTC —
 * `scripts/today-check.mjs` stops the sixteenth call site, and this stops the helper itself from
 * regressing to the arithmetic it replaced.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { todayIn } from "../../src/lib/core/today.ts";

const AMS = "Europe/Amsterdam"; // UTC+2 in summer, UTC+1 in winter
const NZ = "Pacific/Auckland"; // UTC+12 in winter, UTC+13 in summer — the acceptance criterion's
const LA = "America/Los_Angeles"; // UTC−7 in summer: behind UTC, so it fails the other way

describe("the midnight the old arithmetic got wrong", () => {
  test("23:30 UTC is already tomorrow in a UTC+2 tenant", () => {
    const instant = new Date("2026-07-07T23:30:00Z");
    assert.equal(instant.toISOString().slice(0, 10), "2026-07-07"); // what the app used to say
    assert.equal(todayIn(AMS, instant), "2026-07-08");
  });

  test("01:30 CEST is not yesterday — the two-hour window every overdue marker was wrong in", () => {
    // 01:30 on the 8th in Amsterdam is 23:30 on the 7th in UTC.
    const instant = new Date("2026-07-07T23:30:00Z");
    assert.notEqual(todayIn(AMS, instant), "2026-07-07");
    assert.equal(todayIn(AMS, instant), "2026-07-08");
  });

  test("a tenant on Pacific/Auckland with a UTC clock is a whole day ahead", () => {
    // 09:00 UTC on the 7th is 21:00 on the 7th in NZ (+12) — same day…
    assert.equal(todayIn(NZ, new Date("2026-07-07T09:00:00Z")), "2026-07-07");
    // …but 13:00 UTC is already 01:00 on the 8th, which is where a due-today task went missing.
    assert.equal(todayIn(NZ, new Date("2026-07-07T13:00:00Z")), "2026-07-08");
  });

  test("a tenant behind UTC fails in the other direction, which one zone would not have caught", () => {
    // 00:30 UTC on the 8th is still 17:30 on the 7th in Los Angeles.
    assert.equal(todayIn(LA, new Date("2026-07-08T00:30:00Z")), "2026-07-07");
  });
});

describe("the zone is read per call, not frozen", () => {
  test("winter and summer offsets both resolve", () => {
    // 23:30 UTC in January is 00:30 the next day in Amsterdam (+1) — an hour less of window, but
    // the same fault, which is why the offset may never be a constant.
    assert.equal(todayIn(AMS, new Date("2026-01-07T23:30:00Z")), "2026-01-08");
    assert.equal(todayIn(AMS, new Date("2026-01-07T22:30:00Z")), "2026-01-07");
  });

  test("a cached formatter is per zone, so two tenants in one process never share an answer", () => {
    const instant = new Date("2026-07-07T23:30:00Z");
    assert.equal(todayIn(AMS, instant), "2026-07-08");
    assert.equal(todayIn(LA, instant), "2026-07-07");
    assert.equal(todayIn(AMS, instant), "2026-07-08"); // still, after the second zone was seen
  });

  test("the shape is the API's own wire date, zero-padded", () => {
    assert.match(todayIn(AMS, new Date("2026-01-02T12:00:00Z")), /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(todayIn("UTC", new Date("2026-01-02T12:00:00Z")), "2026-01-02");
  });
});
