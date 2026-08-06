/**
 * Shift-click range selection in the shared `DataTable` (#301).
 *
 * The ids this returns are posted straight to a bulk endpoint — approve, reject, re-file — so the
 * rules worth pinning are the ones whose failure is silent: a range that reaches rows the user
 * cannot see, a duplicate id in the batch, or an anchor that no longer exists quietly selecting
 * from wherever index -1 lands.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { rangeSelection } from "../../src/lib/core/table/selection.ts";

const ORDER = ["a", "b", "c", "d", "e"];

describe("rangeSelection", () => {
  test("selects the span between the anchor and the clicked row", () => {
    assert.deepEqual(rangeSelection(ORDER, "b", "d", ["b"]), ["b", "c", "d"]);
  });

  test("reads the same span backwards", () => {
    assert.deepEqual(rangeSelection(ORDER, "d", "b", ["d"]).sort(), ["b", "c", "d"]);
  });

  test("shift-clicking a selected row drops the whole span", () => {
    assert.deepEqual(rangeSelection(ORDER, "a", "c", ["a", "b", "c", "e"]), ["e"]);
  });

  test("keeps selections made outside the span", () => {
    assert.deepEqual(rangeSelection(ORDER, "d", "e", ["a", "d"]), ["a", "d", "e"]);
  });

  test("never lists a row twice — the ids go to a bulk endpoint verbatim", () => {
    // Anchor and a row mid-span are already ticked; the span must absorb them, not re-add them.
    const next = rangeSelection(ORDER, "a", "e", ["a", "c"]);
    assert.deepEqual([...next].sort(), ["a", "b", "c", "d", "e"]);
    assert.equal(new Set(next).size, next.length);
  });

  test("with no anchor it toggles the one row", () => {
    assert.deepEqual(rangeSelection(ORDER, null, "c", []), ["c"]);
    assert.deepEqual(rangeSelection(ORDER, null, "c", ["c"]), []);
  });

  test("an anchor outside the visible order toggles rather than selecting from index -1", () => {
    // A collapsed section, or a row the last page had and this one does not. Measuring from -1
    // would sweep in every row from the top of the list.
    assert.deepEqual(rangeSelection(ORDER, "zz", "d", ["zz"]), ["zz", "d"]);
  });

  test("only the rows that are actually visible can be swept in", () => {
    // The caller hands in the *drawn* order: collapsed groups are already gone from it, so a
    // range across one cannot pick up the rows hiding inside.
    const visible = ["a", "b", "e"]; // c and d sit in a collapsed section
    assert.deepEqual(rangeSelection(visible, "a", "e", ["a"]), ["a", "b", "e"]);
  });
});
