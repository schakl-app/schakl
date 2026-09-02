/**
 * The inline-image marker grammar (`$lib/core/richtext/images.ts`).
 *
 * `![alt](file:<uuid> =50%)` is written by the editor's serializer and read by the renderer's
 * tokenizer, from one shared source string — this test pins the grammar itself, because both
 * of those run only in a browser and a drift between "what is stored" and "what is drawn"
 * would silently destroy an image on the next edit round-trip.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  FILE_IMAGE_SOURCE,
  clampImageWidth,
  cleanImageAlt,
  fileImageMarkdown,
} from "../../src/lib/core/richtext/images.ts";

const ID = "0b54d64e-9f9a-4c69-9d3c-2f6a5f0f7a11";
const RE = new RegExp(`^${FILE_IMAGE_SOURCE}$`);

describe("fileImageMarkdown", () => {
  it("writes the bare marker for natural size", () => {
    assert.equal(fileImageMarkdown(ID, "shot.png", null), `![shot.png](file:${ID})`);
  });

  it("writes the width suffix the tokenizer reads back", () => {
    const marker = fileImageMarkdown(ID, "shot.png", 50);
    assert.equal(marker, `![shot.png](file:${ID} =50%)`);
    const match = RE.exec(marker);
    assert.ok(match, "serializer output must match the shared grammar");
    assert.equal(match[1], "shot.png");
    assert.equal(match[2], ID);
    assert.equal(match[3], "50");
  });

  it("drops a width the renderer would refuse rather than storing it", () => {
    for (const width of [0, 5, 101, 250, Number.NaN, "nonsense"]) {
      const marker = fileImageMarkdown(ID, "x", width);
      assert.equal(marker, `![x](file:${ID})`, `width ${String(width)} must not be stored`);
    }
  });

  it("cannot be broken out of by alt text", () => {
    // Brackets and parens would end the marker early and leave half of it as literal text.
    const marker = fileImageMarkdown(ID, "a](file:evil) [b", 25);
    assert.ok(RE.test(marker), marker);
  });
});

describe("clampImageWidth", () => {
  it("accepts the renderer's range and nothing else", () => {
    assert.equal(clampImageWidth("50"), 50);
    assert.equal(clampImageWidth(100), 100);
    assert.equal(clampImageWidth(10), 10);
    assert.equal(clampImageWidth("9"), null);
    assert.equal(clampImageWidth("101"), null);
    assert.equal(clampImageWidth(""), null);
    assert.equal(clampImageWidth(null), null);
    assert.equal(clampImageWidth(undefined), null);
    assert.equal(clampImageWidth("50px"), 50); // parseInt semantics: the number wins
  });
});

describe("cleanImageAlt", () => {
  it("collapses whitespace and strips the marker's own delimiters", () => {
    assert.equal(cleanImageAlt("  a   b  "), "a b");
    assert.equal(cleanImageAlt("shot [1] (final).png"), "shot 1 final .png");
  });
});
