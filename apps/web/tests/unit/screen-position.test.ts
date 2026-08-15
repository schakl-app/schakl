/**
 * Returning to a screen you were just on (`$lib/core/screen-position.ts`).
 *
 * Every rule here is one that fails silently in a browser: a crumb that goes to page 1 looks
 * exactly like a crumb that works, and a scroll restored on the wrong URL looks like a page that
 * happened to load scrolled. The cases below are the four that decide whether the feature is a
 * feature or a nuisance — the last view of a list wins, an exact URL match is required, the store
 * is bounded, and junk in storage degrades to "nothing remembered" rather than to an exception in
 * the middle of a navigation.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  MAX_POSITIONS,
  parsePositions,
  remember,
  restoreOffset,
  returnQueriesOf,
  type ScreenPosition,
} from "../../src/lib/core/screen-position.ts";

const at = (path: string, search = "", y = 0): ScreenPosition => ({ path, search, y });

describe("remember", () => {
  test("keeps one entry per screen — the last view of a list is the one to return to", () => {
    // Paged 3 → 5, then opened a record. Coming back to page 3 would be returning to a screen
    // they had already left.
    let positions = remember([], "/companies", "?page=3", 800);
    positions = remember(positions, "/companies", "?page=5", 1200);

    assert.deepEqual(positions, [{ path: "/companies", search: "?page=5", y: 1200 }]);
  });

  test("moves the screen just left to the front, so eviction drops the least recently left", () => {
    let positions = remember([], "/tasks", "?status=open", 100);
    positions = remember(positions, "/companies", "?page=2", 200);
    positions = remember(positions, "/tasks", "?status=open", 300);

    assert.deepEqual(
      positions.map((entry) => entry.path),
      ["/tasks", "/companies"],
    );
  });

  test("is bounded — a long session's wandering cannot grow an unbounded blob", () => {
    let positions: ScreenPosition[] = [];
    for (let i = 0; i < MAX_POSITIONS + 15; i++) {
      positions = remember(positions, `/screen-${i}`, "?page=2", i + 1);
    }

    assert.equal(positions.length, MAX_POSITIONS);
    assert.equal(positions[0].path, `/screen-${MAX_POSITIONS + 14}`);
  });

  test("normalises the offset — a fractional or negative scroll is not a position", () => {
    assert.equal(remember([], "/companies", "", 812.4)[0].y, 812);
    assert.equal(remember([], "/companies", "", -30)[0].y, 0);
    assert.equal(remember([], "/companies", "", Number.NaN)[0].y, 0);
  });
});

describe("returnQueriesOf", () => {
  test("offers only the screens that had a slice — a bare path needs no help", () => {
    const positions = [at("/companies", "?page=3&status=active", 800), at("/tasks", "", 40)];

    assert.deepEqual(returnQueriesOf(positions), { "/companies": "?page=3&status=active" });
  });
});

describe("restoreOffset", () => {
  const positions = [at("/companies", "?page=3", 940), at("/invoices", "", 620)];

  test("restores when the URL landed on is the URL that was left", () => {
    assert.equal(restoreOffset(positions, { pathname: "/companies", search: "?page=3" }), 940);
  });

  test("does not restore on the section's front page — that is what the sidebar goes to", () => {
    // The crumb carries `?page=3`; the nav item does not. Same pathname, different screen.
    assert.equal(restoreOffset(positions, { pathname: "/companies", search: "" }), null);
  });

  test("does not restore across a filter or page change — a new slice starts at the top", () => {
    assert.equal(restoreOffset(positions, { pathname: "/companies", search: "?page=4" }), null);
    assert.equal(
      restoreOffset(positions, { pathname: "/companies", search: "?page=3&q=acme" }),
      null,
    );
  });

  test("says nothing for a screen never visited, or one left at the top", () => {
    assert.equal(restoreOffset(positions, { pathname: "/domains", search: "" }), null);
    assert.equal(restoreOffset([at("/tasks", "", 0)], { pathname: "/tasks", search: "" }), null);
  });
});

describe("parsePositions", () => {
  test("round-trips what was written", () => {
    const positions = remember([], "/companies", "?page=3", 940);

    assert.deepEqual(parsePositions(JSON.stringify(positions)), positions);
  });

  test("degrades to nothing remembered rather than throwing mid-navigation", () => {
    for (const raw of [null, "", "{oops", '{"path":"/x"}', "[null]", '[{"path":"/x"}]']) {
      assert.deepEqual(parsePositions(raw), [], `for ${JSON.stringify(raw)}`);
    }
  });

  test("keeps the well-formed entries out of a partly-rotten array", () => {
    const raw = JSON.stringify([{ path: "/companies", search: "?page=3", y: 940 }, { junk: true }]);

    assert.deepEqual(parsePositions(raw), [{ path: "/companies", search: "?page=3", y: 940 }]);
  });
});
