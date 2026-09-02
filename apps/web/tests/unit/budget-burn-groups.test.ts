/**
 * The dashboard's budget partition (`$lib/modules/projects/burn-groups.ts`) and the spilling bar
 * it draws (`core/burn.ts`, `burnOverflowBar`).
 *
 * Three things here are invisible in a screenshot and were the donut's actual faults, which is
 * why they are pinned.
 *
 * **The partition is the one burn scale.** The bands are `burnLevel`'s three answers, so a row
 * drawn amber by its bar can never sit under a heading counted as green — and every budgeted
 * row lands in exactly one band while an unbudgeted one lands in none.
 *
 * **A heading's URL token is the API's.** `?burn=over|warn|ok` is what the list page's pill and
 * the API's filter both read; the token parser is the one both the page load and the export use,
 * and it falls back rather than raising on a query string anyone can edit.
 *
 * **The bar spills, and it spills by the right amount.** The budget line is at two thirds, a row
 * inside its budget never reaches it, a row over it crosses by its overrun up to the track's end
 * — and the number beside it stays unclamped, which is `burnPct`'s existing promise.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { burnLevel, burnOverflowBar, burnPct } from "../../src/lib/core/burn.ts";
import {
  BURN_GROUP_STATE,
  BURN_GROUPS,
  burnFilterToken,
  burnGroupHref,
  burnGroupLabelKey,
  groupByBurn,
} from "../../src/lib/modules/projects/burn-groups.ts";

const row = (id: string, spent: number, budget: number | null) => ({
  id,
  hours: { spent_hours: spent, budget_hours: budget },
});

describe("the partition", () => {
  test("is the burn scale's three bands, hottest first", () => {
    assert.deepEqual([...BURN_GROUPS], ["over", "warn", "ok"]);
    for (const level of BURN_GROUPS) {
      assert.equal(level, burnLevel({ over: 130, warn: 80, ok: 20 }[level]));
    }
  });

  test("puts every budgeted row in exactly one band and drops the unbudgeted", () => {
    const rows = [
      row("a", 48, 16), // 300 %
      row("b", 16, 16), // exactly on the budget is over: nothing is left
      row("c", 12, 16), // 75 % is the first amber step
      row("d", 11.9, 16), // just under it stays green
      row("e", 0, 16),
      row("f", 40, null), // no budget: nothing to be a share of
      row("g", 5, 0),
    ];
    const groups = groupByBurn(rows);
    assert.deepEqual(Object.fromEntries(BURN_GROUPS.map((l) => [l, groups[l].map((r) => r.id)])), {
      over: ["a", "b"],
      warn: ["c"],
      ok: ["d", "e"],
    });
    const placed = BURN_GROUPS.flatMap((l) => groups[l].map((r) => r.id));
    assert.equal(new Set(placed).size, placed.length, "no row lands in two bands");
    assert.equal(placed.length, rows.filter((r) => r.hours.budget_hours).length);
  });

  test("keeps the API's order inside a band", () => {
    const groups = groupByBurn([row("hot", 30, 10), row("warm", 20, 10), row("cool", 11, 10)]);
    assert.deepEqual(
      groups.over.map((r) => r.id),
      ["hot", "warm", "cool"],
    );
  });

  test("draws over as a claim, almost as a warning and room as fine — never neutral", () => {
    assert.deepEqual(BURN_GROUP_STATE, { over: "late", warn: "soon", ok: "ok" });
  });
});

describe("the headings' destinations", () => {
  test("open the list narrowed to the same band and the same status set", () => {
    for (const level of BURN_GROUPS) {
      assert.equal(burnGroupHref(level), `/projects?burn=${level}&status=active`);
      assert.equal(burnGroupLabelKey(level), `projects.filter.burn.${level}`);
    }
  });

  test("the token parser honours the three bands and nothing else", () => {
    for (const level of BURN_GROUPS) assert.equal(burnFilterToken(level), level);
    assert.equal(burnFilterToken("hot"), undefined);
    assert.equal(burnFilterToken(""), undefined);
    assert.equal(burnFilterToken(null), undefined);
    assert.equal(burnFilterToken(undefined), undefined);
  });
});

describe("the spilling bar", () => {
  test("puts the budget line at two thirds of the track", () => {
    const bar = burnOverflowBar(50);
    assert.ok(bar);
    assert.ok(Math.abs(bar.mark - 66.67) < 0.01);
  });

  test("a row inside its budget never reaches the line", () => {
    const bar = burnOverflowBar(80);
    assert.ok(bar);
    assert.ok(bar.fill < bar.mark);
    assert.equal(bar.spill, 0);
    assert.ok(Math.abs(bar.fill - (80 / 150) * 100) < 0.01);
  });

  test("a row on its budget fills exactly to the line and spills nothing", () => {
    const bar = burnOverflowBar(100);
    assert.ok(bar);
    assert.ok(Math.abs(bar.fill - bar.mark) < 0.01);
    assert.equal(bar.spill, 0);
  });

  test("a row over its budget spills past the line by its overrun", () => {
    const bar = burnOverflowBar(125);
    assert.ok(bar);
    assert.ok(Math.abs(bar.fill - bar.mark) < 0.01);
    assert.ok(Math.abs(bar.spill - (25 / 150) * 100) < 0.01);
    assert.ok(Math.abs(bar.fill + bar.spill - (125 / 150) * 100) < 0.01);
  });

  test("the bar clamps at the track's end; the number does not", () => {
    const bar = burnOverflowBar(300);
    assert.ok(bar);
    assert.ok(Math.abs(bar.fill + bar.spill - 100) < 0.01);
    assert.equal(burnPct(48, 16), 300);
  });

  test("no budget draws no bar", () => {
    assert.equal(burnOverflowBar(null), null);
    assert.equal(burnOverflowBar(burnPct(5, null)), null);
  });
});
