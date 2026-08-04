/**
 * The two quick-add rules that are invisible on screen (#246).
 *
 * `shouldKeepPrefill` guards the one regression the reordering in `aiQuickAdd` can cause: the
 * page wipes AI state when the selected day changes, and the quick add now fills the form
 * *before* navigating to the parsed day. Get this wrong and "gisteren 2 uur" fills the form and
 * then instantly blanks it — which looks exactly like the parse having failed.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { endOfDay, nextStartFrom, shouldKeepPrefill } from "../../src/lib/modules/time/quickadd.ts";

describe("shouldKeepPrefill", () => {
  test("a prefill survives the navigation it caused", () => {
    // openPrefilled recorded 2026-03-16, then goto() moved the view there.
    assert.equal(shouldKeepPrefill("2026-03-16", "2026-03-16"), true);
  });

  test("a prefill does not survive the user moving to another day", () => {
    assert.equal(shouldKeepPrefill("2026-03-16", "2026-03-17"), false);
  });

  test("no prefill is nothing to keep", () => {
    assert.equal(shouldKeepPrefill(null, "2026-03-16"), false);
  });
});

describe("nextStartFrom", () => {
  test("a duration-only entry starts after the last thing logged that day", () => {
    const entries = [
      { started_at: "2026-03-16T09:00:00Z", ended_at: "2026-03-16T10:30:00Z" },
      { started_at: "2026-03-16T11:00:00Z", ended_at: "2026-03-16T13:15:00Z" },
    ];
    const start = nextStartFrom(entries);
    // Rendered in the viewer's zone, so assert the shape and that it is the later of the two.
    assert.match(start, /^\d{2}:\d{2}$/);
    assert.equal(start, endOfDay(entries));
  });

  test("an empty day falls back rather than guessing", () => {
    assert.equal(nextStartFrom([], "08:30"), "08:30");
  });

  test("a running timer has no end, so it never becomes the start", () => {
    assert.equal(nextStartFrom([{ started_at: "2026-03-16T09:00:00Z", ended_at: null }]), "09:00");
  });

  test("out-of-order entries still yield the latest end", () => {
    const late = { started_at: "2026-03-16T14:00:00Z", ended_at: "2026-03-16T16:00:00Z" };
    const early = { started_at: "2026-03-16T09:00:00Z", ended_at: "2026-03-16T10:00:00Z" };
    assert.equal(endOfDay([late, early]), endOfDay([early, late]));
  });
});
