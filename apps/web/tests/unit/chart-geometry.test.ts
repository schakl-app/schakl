/**
 * The box a hand-rolled inline-SVG chart draws into (`$lib/core/ui/charts/geometry.ts`).
 *
 * This is the shape that is invisible in a screenshot taken on a laptop: with a constant
 * `viewBox` the marketing trend chart rendered 3130×869 with 59px axis labels on a 3178px screen
 * and 6px labels on a 390px phone, and every functional test passed either way — the SVG was
 * valid, the series was right, only the size was absurd. So the rule is pinned at the widths
 * nobody develops at.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import * as geometry from "../../src/lib/core/ui/charts/geometry.ts";

const { barWidth, chartWidth } = geometry;

// The two charts' own settings, so a change to either is a change to this file.
const TREND = { fallback: 720, min: 280 };
const BARS = { fallback: 760, min: 320 };

describe("chartWidth", () => {
  test("an unmeasured container draws at the design width, not at zero", () => {
    // SSR and the first client frame both hand us 0; a 0-wide viewBox is a blank chart.
    assert.equal(chartWidth(0, TREND.fallback, TREND.min), 720);
    assert.equal(chartWidth(0, BARS.fallback, BARS.min), 760);
  });

  test("a measured container is taken verbatim — the chart fills the width it is given", () => {
    assert.equal(chartWidth(1232, TREND.fallback, TREND.min), 1232);
    assert.equal(chartWidth(1552, TREND.fallback, TREND.min), 1552);
  });

  test("a collapsed or absurdly narrow box is floored so the plot area stays positive", () => {
    assert.equal(chartWidth(40, TREND.fallback, TREND.min), 280);
    // The trend chart's own padding is 64px; the floor has to clear it with room to draw.
    assert.ok(chartWidth(0, TREND.fallback, TREND.min) - 64 > 0);
    assert.ok(chartWidth(40, TREND.fallback, TREND.min) - 64 > 0);
  });
});

describe("the height is not a function of the width", () => {
  // An earlier pass grew the height with the container, which is a scrollbar-oscillation
  // hazard: taller chart -> taller page -> scrollbar -> narrower container -> shorter chart ->
  // no scrollbar -> flicker, forever, on whichever screen sits at the knife-edge. Both charts
  // now hold a constant design height, and this is the guard against quietly reintroducing one.
  test("geometry exports no width-to-height function", () => {
    assert.deepEqual(Object.keys(geometry).sort(), ["barWidth", "chartWidth"]);
  });
});

describe("barWidth", () => {
  test("the design width is unchanged at the width the chart was drawn at", () => {
    // plotW at 760 wide is 696; twelve slots of 58.
    assert.equal(barWidth(696 / 12, 14, 8), 14);
  });

  test("a bar grows with its slot, so a wide chart is not twelve threads", () => {
    // plotW at the shell's 1600px content measure is ~1488; slots of ~124.
    const wide = barWidth(1488 / 12, 14, 8);
    assert.ok(wide > 25 && wide < 35, `expected a chunky bar, got ${wide}`);
  });

  test("a pair of bars plus the gap always fits inside its slot", () => {
    for (let slot = 12; slot <= 400; slot += 3) {
      assert.ok(barWidth(slot, 14, 8) * 2 + 8 <= slot + 1e-9, `overflowed at slot ${slot}`);
    }
  });

  test("a narrow phone slot shrinks the bar rather than overlapping its neighbour", () => {
    // plotW at 342 wide is 278; slots of ~23 — below the 14px design width.
    assert.ok(barWidth(278 / 12, 14, 8) < 14);
  });

  test("a bar is never negative, however cramped the slot", () => {
    for (const slot of [0, 1, 4, 8, 9]) {
      assert.ok(barWidth(slot, 14, 8) <= slot / 2, `slot ${slot} gave ${barWidth(slot, 14, 8)}`);
    }
  });
});
