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

import { barWidth, chartHeight, chartWidth } from "../../src/lib/core/ui/charts/geometry.ts";

// The two charts' own settings, so a change to either is a change to this file.
const TREND = { fallback: 720, min: 280, base: 200, cap: 300 };
const BARS = { fallback: 760, min: 320, base: 240, cap: 340 };

describe("chartWidth", () => {
  test("an unmeasured container draws at the design width, not at zero", () => {
    // SSR and the first client frame both hand us 0; a 0-wide viewBox is a blank chart.
    assert.equal(chartWidth(0, TREND.fallback, TREND.min), 720);
  });

  test("a measured container is taken verbatim — the chart fills the width it is given", () => {
    assert.equal(chartWidth(1232, TREND.fallback, TREND.min), 1232);
    assert.equal(chartWidth(3130, TREND.fallback, TREND.min), 3130);
  });

  test("a collapsed or absurdly narrow box is floored so the plot area stays positive", () => {
    assert.equal(chartWidth(40, TREND.fallback, TREND.min), 280);
  });
});

describe("chartHeight", () => {
  test("the design width keeps the design height exactly", () => {
    // The regression guard for "the fix redesigned the chart on a laptop".
    assert.equal(chartHeight(720, TREND.base, TREND.cap), 200);
    assert.equal(chartHeight(760, BARS.base, BARS.cap), 240);
  });

  test("a narrow container gets the full height rather than a squashed one", () => {
    // The phone half of the bug: a locked aspect ratio made this 342×95.
    assert.equal(chartHeight(342, TREND.base, TREND.cap), 200);
    assert.equal(chartHeight(342, BARS.base, BARS.cap), 240);
  });

  test("a very wide container is capped — the bug was a chart taller than the viewport", () => {
    assert.equal(chartHeight(3130, TREND.base, TREND.cap), 300);
    assert.equal(chartHeight(3130, BARS.base, BARS.cap), 340);
    // What it used to do at that width, and must never do again.
    assert.ok(chartHeight(3130, TREND.base, TREND.cap) < 869);
  });

  test("height never outruns the cap however wide the screen gets", () => {
    for (const w of [1280, 1920, 2560, 3178, 5120, 7680]) {
      const h = chartHeight(w, TREND.base, TREND.cap);
      assert.ok(h >= TREND.base && h <= TREND.cap, `${w}px gave ${h}`);
    }
  });

  test("height is monotonic in width — a wider chart is never a shorter one", () => {
    let previous = 0;
    for (let w = 280; w <= 4000; w += 37) {
      const h = chartHeight(w, TREND.base, TREND.cap);
      assert.ok(h >= previous, `height fell from ${previous} to ${h} at ${w}px`);
      previous = h;
    }
  });

  test("it returns whole pixels — a fractional viewBox height blurs the gridlines", () => {
    assert.equal(chartHeight(999, TREND.base, TREND.cap), Math.round(chartHeight(999, 200, 300)));
    assert.ok(Number.isInteger(chartHeight(999, TREND.base, TREND.cap)));
  });
});

describe("barWidth", () => {
  test("the design width is unchanged at the width the chart was drawn at", () => {
    // plotW at 760 wide is 696; twelve slots of 58.
    assert.equal(barWidth(696 / 12, 14, 8), 14);
  });

  test("a bar grows with its slot, so a wide chart is not twelve threads", () => {
    // plotW at 3130 wide is 3066; slots of ~255.
    const wide = barWidth(3066 / 12, 14, 8);
    assert.ok(wide > 50 && wide < 70, `expected a chunky bar, got ${wide}`);
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
});
