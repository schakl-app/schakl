/**
 * The base64 framing the dictated clip travels in (#246).
 *
 * The API sniffs the container from the decoded bytes and rejects anything it does not
 * recognise, so a framing bug here surfaces as "that audio format is not supported" — a
 * message pointing at the wrong thing entirely. Cheap to pin, expensive to debug.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { stripDataUrl } from "../../src/lib/core/voice/encode.ts";

describe("stripDataUrl", () => {
  test("drops the data-URL prefix a FileReader produces", () => {
    assert.equal(stripDataUrl("data:audio/webm;codecs=opus;base64,GkXfow=="), "GkXfow==");
  });

  test("leaves a bare base64 payload alone", () => {
    assert.equal(stripDataUrl("GkXfow=="), "GkXfow==");
  });

  test("keeps base64 that itself contains no comma intact", () => {
    // A WebM header round-trips: 0x1A45DFA3 is what the API's sniffer looks for.
    const encoded = Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x01, 0x02]).toString("base64");
    const stripped = stripDataUrl(`data:audio/webm;base64,${encoded}`);
    assert.equal(stripped, encoded);
    assert.deepEqual([...Buffer.from(stripped, "base64").subarray(0, 4)], [0x1a, 0x45, 0xdf, 0xa3]);
  });
});
