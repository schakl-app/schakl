/**
 * An instant → the org-local day and clock (`$lib/core/wallclock`), pinned at the boundaries
 * that made the time module's `started_at.slice(11, 16)` wrong: a summer offset, a winter
 * offset, and an evening that is already tomorrow in UTC.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { wallClockIn } from "../../src/lib/core/wallclock.ts";

describe("wallClockIn", () => {
  test("a summer instant reads two hours later in Amsterdam", () => {
    assert.deepEqual(wallClockIn("2026-09-19T10:40:00Z", "Europe/Amsterdam"), {
      day: "2026-09-19",
      time: "12:40",
    });
  });

  test("a winter instant reads one hour later", () => {
    assert.deepEqual(wallClockIn("2026-01-19T11:40:00Z", "Europe/Amsterdam"), {
      day: "2026-01-19",
      time: "12:40",
    });
  });

  test("an evening in UTC is already the next local day", () => {
    assert.deepEqual(wallClockIn("2026-07-06T22:30:00Z", "Europe/Amsterdam"), {
      day: "2026-07-07",
      time: "00:30",
    });
  });

  test("midnight prints as 00, never 24", () => {
    assert.equal(wallClockIn("2026-07-06T22:00:00Z", "Europe/Amsterdam").time, "00:00");
  });

  test("UTC is the identity", () => {
    assert.deepEqual(wallClockIn("2026-07-06T09:12:00Z", "UTC"), {
      day: "2026-07-06",
      time: "09:12",
    });
  });
});
