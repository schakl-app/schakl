/**
 * How a fixed-layout grid divides its width (`$lib/core/table/widths.ts`).
 *
 * Pinned because this is invisible everywhere it matters: every row renders, every value is
 * right, the API is untouched — only the columns are absurd, and only at a width the person who
 * wrote the change was not using. #346 has been fixed twice already (the identity column handed
 * zero, then the identity column handed its floor while a column of em-dashes kept 99 %), so the
 * rules that came out of it are asserted here rather than only measured in a browser once.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import * as widths from "../../src/lib/core/table/widths.ts";

const { FIXED_MIN, FLEX_MIN, FLEX_TARGET, tableLayout } = widths;

/** /tasks' own columns, so a change to that list is a change to this file. */
const TITLE = { key: "title", primary: true, width: 360 };
const OPTIONAL = [
  { key: "labels", width: 200 },
  { key: "assignee", width: 180 },
  { key: "priority", width: 110 },
  { key: "due_date", width: 120 },
  { key: "project", width: 180 },
  { key: "company", width: 180 },
  { key: "checklist", width: 100 },
  { key: "comments", width: 100 },
  { key: "allocated", width: 110 },
  { key: "created_at", width: 120 },
];
const CHROME = 52; // the ⋯ gutter; no checkbox column on a list that is not being picked
const ALL = [TITLE, ...OPTIONAL];
const DEFAULTS = [TITLE, ...OPTIONAL.slice(0, 6)];

/**
 * What the flexible column actually renders at: the box, less every fixed column and the
 * gutters. That — not `flexFloor`, which is only the table's `min-width` guarantee — is the
 * number the user reads a task's name in.
 */
const allotment = (layout: widths.TableLayout, columns: widths.WidthColumn[], viewport: number) =>
  Math.max(viewport, layout.minWidth) -
  columns.reduce((s, c) => s + (layout.widths[c.key] ?? 0), 0) -
  CHROME;

describe("tableLayout", () => {
  test("unmeasured is unshrunk, so the SSR HTML is what it always was", () => {
    const layout = tableLayout(ALL, {}, 0, CHROME);
    assert.equal(layout.shrink, 1);
    assert.equal(layout.widths.labels, 200);
    assert.equal(layout.widths.title, undefined); // auto: it absorbs the slack
  });

  test("with room to spare, every declared width is honoured exactly", () => {
    const layout = tableLayout(ALL, {}, 1812, CHROME);
    assert.equal(layout.shrink, 1);
    assert.equal(layout.widths.created_at, 120);
    assert.equal(allotment(layout, ALL, 1812), 360); // the title's own declared width
  });

  test("the page it asks for is the sum of the claims, and it fits inside it", () => {
    const layout = tableLayout(ALL, {}, 0, CHROME);
    assert.equal(layout.natural, 360 + 1400 + CHROME + 2);
    // Granted that width, nothing shrinks: the claim and the layout agree by construction.
    assert.equal(tableLayout(ALL, {}, layout.natural - 2, CHROME).shrink, 1);
  });

  test("the record's name is not the column that pays for a shortfall", () => {
    // Twelve columns inside the 1600px measure. Before f984058b, Titel sat at exactly FLEX_MIN
    // while `labels` kept 198 of its 200 and nine titles of eleven truncated.
    const layout = tableLayout(ALL, {}, 1598, CHROME);
    assert.ok(layout.shrink < 1);
    assert.ok(
      allotment(layout, ALL, 1598) >= FLEX_TARGET,
      `the name should hold its reservation, got ${allotment(layout, ALL, 1598)}`,
    );
    assert.ok(layout.widths.labels! < 200, "an optional column gives way instead");
  });

  test("the reservation holds wherever the fixed columns can still buy it", () => {
    for (let viewport = 900; viewport <= 1900; viewport += 7) {
      const layout = tableLayout(ALL, {}, viewport, CHROME);
      const floored = OPTIONAL.some((c) => layout.widths[c.key] === FIXED_MIN);
      if (floored) continue; // nothing left to take: the floor stands and the grid scrolls
      assert.ok(
        allotment(layout, ALL, viewport) >= FLEX_TARGET,
        `name got ${allotment(layout, ALL, viewport)} in a ${viewport}px box`,
      );
    }
  });

  test("every column keeps its floor, and below that the grid scrolls rather than vanishing", () => {
    const layout = tableLayout(ALL, {}, 700, CHROME);
    assert.equal(layout.flexFloor, FLEX_MIN);
    for (const column of OPTIONAL) {
      assert.equal(layout.widths[column.key], FIXED_MIN, `${column.key} floored`);
    }
    // #346's original failure: the identity column allotted zero. The min-width prevents it.
    assert.ok(layout.minWidth > 700);
    assert.ok(allotment(layout, ALL, 700) >= FLEX_MIN);
  });

  test("a dragged width is an instruction: never shrunk, never floored", () => {
    const layout = tableLayout(ALL, { labels: 400, title: 500 }, 900, CHROME);
    assert.equal(layout.widths.labels, 400);
    assert.equal(layout.flexFloor, 500);
    assert.ok(layout.shrink < 1, "the rest still gives way around it");
  });

  test("the default column set reads well at every ordinary width", () => {
    for (const viewport of [1168, 1328, 1598, 1812]) {
      const layout = tableLayout(DEFAULTS, {}, viewport, CHROME);
      assert.ok(
        allotment(layout, DEFAULTS, viewport) >= FLEX_TARGET,
        `name got ${allotment(layout, DEFAULTS, viewport)} at ${viewport}`,
      );
    }
  });

  test("shrunken widths round down, so they never sum past the box", () => {
    for (let viewport = 1100; viewport <= 1600; viewport += 7) {
      const layout = tableLayout(ALL, {}, viewport, CHROME);
      const declared = ALL.reduce((s, c) => s + (layout.widths[c.key] ?? 0), 0) + CHROME;
      assert.ok(
        declared + FLEX_MIN <= Math.max(viewport, layout.minWidth),
        `the fixed columns overshoot their own min-width at ${viewport}`,
      );
    }
  });

  test("the flexible column is the one that says so, else the primary, else the first", () => {
    assert.equal(tableLayout(ALL, {}, 0, 0).flexKey, "title");
    assert.equal(
      tableLayout([{ key: "a", width: 100 }, { key: "b", flex: true }, TITLE], {}, 0, 0).flexKey,
      "b",
    );
    assert.equal(tableLayout([{ key: "a", width: 100 }], {}, 0, 0).flexKey, "a");
  });
});
