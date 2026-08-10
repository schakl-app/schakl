/**
 * Which calendar periods the marketing picker offers (`$lib/modules/marketing/period-tokens.ts`).
 *
 * Two backwards walks, and both of them cross a year boundary. That is the whole reason this file
 * exists: the naive `year--, unit--` produces "2026-Q0" and month "00" on exactly the inputs
 * nobody clicks while developing in August, and an option list with a nonsense token in it looks
 * completely normal until somebody picks it and the API quietly falls back to 30 days.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  MONTH_OPTIONS,
  monthTokens,
  QUARTER_OPTIONS,
  quarterOf,
  quarterTokens,
} from "../../src/lib/modules/marketing/period-tokens.ts";

describe("monthTokens", () => {
  test("starts with the anchor's own month, newest first", () => {
    assert.deepEqual(monthTokens("2026-08-10", 3), ["2026-08", "2026-07", "2026-06"]);
  });

  test("walks back across the year boundary without producing month 00", () => {
    assert.deepEqual(monthTokens("2026-02-15", 4), ["2026-02", "2026-01", "2025-12", "2025-11"]);
  });

  test("reaches the same month a year earlier, which is what the comparison needs", () => {
    const tokens = monthTokens("2026-08-10");
    assert.equal(tokens.length, MONTH_OPTIONS);
    assert.ok(tokens.includes("2025-08"), tokens.join(", "));
  });

  test("every token is a month the API can parse", () => {
    for (const token of monthTokens("2026-01-01", 26)) {
      assert.match(token, /^\d{4}-(0[1-9]|1[0-2])$/, token);
    }
  });
});

describe("quarterTokens", () => {
  test("starts with the anchor's own quarter, newest first", () => {
    assert.deepEqual(quarterTokens("2026-08-10", 3), ["2026-Q3", "2026-Q2", "2026-Q1"]);
  });

  test("walks back across the year boundary without producing Q0", () => {
    assert.deepEqual(quarterTokens("2026-01-15", 3), ["2026-Q1", "2025-Q4", "2025-Q3"]);
  });

  test("every token is a quarter the API can parse, and they step back one at a time", () => {
    const tokens = quarterTokens("2026-03-31", 12);
    for (const token of tokens) assert.match(token, /^\d{4}-Q[1-4]$/, token);
    // Consecutive and strictly descending: three years of quarters with none repeated or skipped.
    const ordinals = tokens.map((t) => Number(t.slice(0, 4)) * 4 + Number(t.slice(6)));
    assert.deepEqual(
      ordinals,
      ordinals.map((_, i) => ordinals[0] - i),
    );
  });

  test("the list is long enough to reach the same quarter last year", () => {
    const tokens = quarterTokens("2026-08-10");
    assert.equal(tokens.length, QUARTER_OPTIONS);
    assert.ok(tokens.includes("2025-Q3"), tokens.join(", "));
  });
});

describe("quarterOf", () => {
  test("puts each month in its quarter, both boundaries included", () => {
    assert.deepEqual(
      ["01", "03", "04", "06", "07", "09", "10", "12"].map((m) => quarterOf(`2026-${m}-01`)),
      [1, 1, 2, 2, 3, 3, 4, 4],
    );
  });
});
