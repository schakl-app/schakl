/**
 * Personal calendar colours (#281): a viewer may recolour a feed to any hue, so a colour is now
 * either a named token (a Tailwind class pair) or a raw `#hex` (an inline `--evc` style). This
 * covers the single resolver both paths run through — that a token still yields its class and a
 * hex never leaks into a class name (which would be a dead Tailwind utility, invisible on the
 * chip). `eventChipParts` (the holiday/tentative wrapper) is thin and delegates straight to
 * these, so it is left to svelte-check and the browser smoke.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { isHexColor, labelChipParts, labelDotParts } from "../../src/lib/core/ui/colors.ts";

describe("isHexColor", () => {
  test("accepts 3- and 6-digit hexes, any case", () => {
    for (const hex of ["#fff", "#7c3aed", "#7C3AED", "#000000"]) {
      assert.equal(isHexColor(hex), true, hex);
    }
  });

  test("rejects tokens, junk and partial hexes", () => {
    for (const value of ["sky", "red", "", "#", "#12", "#gggggg", "7c3aed", null, undefined]) {
      assert.equal(isHexColor(value), false, String(value));
    }
  });
});

describe("labelChipParts / labelDotParts", () => {
  test("a token resolves to its Tailwind class, no inline style", () => {
    const chip = labelChipParts("sky");
    assert.match(chip.class, /bg-sky-100/);
    assert.equal(chip.style, "");
    const dot = labelDotParts("sky");
    assert.match(dot.class, /bg-sky-500/);
    assert.equal(dot.style, "");
  });

  test("a hex resolves to the custom class + an --evc style, never a Tailwind class", () => {
    const chip = labelChipParts("#7c3aed");
    assert.equal(chip.class, "cal-chip-custom");
    assert.equal(chip.style, "--evc:#7c3aed");
    const dot = labelDotParts("#7c3aed");
    assert.equal(dot.class, "cal-dot-custom");
    assert.equal(dot.style, "--evc:#7c3aed");
  });

  test("an unknown token falls back to a neutral class, still no style", () => {
    // e.g. the holidays feed's `slate`, which is not in the palette map.
    const chip = labelChipParts("slate");
    assert.equal(chip.style, "");
    assert.doesNotMatch(chip.class, /cal-chip-custom/);
  });
});
