/**
 * The dictation helper's pure half (`core/voice/transcribe.ts`).
 *
 * The case worth pinning is the 413: it is answered by two different layers — the API with its
 * envelope, and the SvelteKit server with a text body when the clip is over adapter-node's
 * `BODY_SIZE_LIMIT` — and the second has no envelope to read. Before the helper, every host read
 * that as "the AI service did not answer", which is the wrong sentence about the right fact.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { formatClock, transcribeFailureKey } from "../../src/lib/core/voice/transcribe.ts";

describe("transcribeFailureKey", () => {
  test("a 413 is 'too long' whether or not a body came with it", () => {
    assert.deepEqual(transcribeFailureKey(413, null), {
      error: "errors.ai_audio_too_large",
      budget: false,
    });
    assert.deepEqual(
      transcribeFailureKey(413, { error: { code: "validation", message: "errors.validation" } }),
      { error: "errors.ai_audio_too_large", budget: false },
    );
  });

  test("the budget refusal is a flag, not an error line", () => {
    assert.deepEqual(
      transcribeFailureKey(409, {
        error: { code: "ai_budget_reached", message: "errors.ai_budget_reached" },
      }),
      { error: null, budget: true },
    );
  });

  test("any other refusal carries the envelope's own key, else the generic one", () => {
    assert.deepEqual(
      transcribeFailureKey(409, {
        error: { code: "ai_speech_not_configured", message: "errors.ai_speech_not_configured" },
      }),
      { error: "errors.ai_speech_not_configured", budget: false },
    );
    assert.deepEqual(transcribeFailureKey(502, null), {
      error: "errors.ai_provider_error",
      budget: false,
    });
  });
});

describe("formatClock", () => {
  test("reads as a clock, so a cap of 300 s prints as 5:00", () => {
    assert.equal(formatClock(0), "0:00");
    assert.equal(formatClock(7), "0:07");
    assert.equal(formatClock(299), "4:59");
    assert.equal(formatClock(300), "5:00");
    assert.equal(formatClock(-3), "0:00");
  });
});
