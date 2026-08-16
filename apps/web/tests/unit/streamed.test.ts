/**
 * `streamed()` — the envelope a streamed section's failure has to arrive in.
 *
 * Tested apart from any page because the case that matters is the one no page can be made to
 * show on demand: the API not answering. A streamed promise that *rejects* is delivered after
 * the shell is already on the wire, so the browser's `.then` never runs, the pending flag never
 * clears, and the screen sits on "Laden…" — invisible in review (the `.then` reads as correct),
 * invisible in every test that stubs a working API, and visible only during a redeploy.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { streamed } from "../../src/lib/core/errors.ts";

describe("streamed", () => {
  test("an answer passes through with no error key", async () => {
    const result = await streamed(Promise.resolve({ data: { rows: [1, 2] } }));
    assert.deepEqual(result, { data: { rows: [1, 2] }, errorKey: null });
  });

  test("the API's own envelope reaches the page as its i18n key", async () => {
    // What a refused Google call looks like: an answer, and the answer is no. The screen draws
    // this one — "reconnect", "the developer token is not approved" — so the key must survive.
    const result = await streamed(
      Promise.resolve({ error: { error: { code: "x", message: "google_ads.errors.reconnect" } } }),
    );
    assert.equal(result.data, null);
    assert.equal(result.errorKey, "google_ads.errors.reconnect");
  });

  test("an envelope that names no key falls back to the read's own sentence", async () => {
    const result = await streamed(Promise.resolve({ error: { detail: "nope" } }));
    assert.equal(result.errorKey, "errors.server");
  });

  test("a thrown fetch settles rather than rejecting", async () => {
    // The redeploy case. openapi-fetch lets a network failure propagate, so without this the
    // page's pending flag has nothing to clear it and stays up for the life of the tab.
    const result = await streamed(Promise.reject(new TypeError("fetch failed")));
    assert.deepEqual(result, { data: null, errorKey: "errors.server" });
  });

  test("the caller may name the sentence a failure gets", async () => {
    const result = await streamed(Promise.reject(new Error("down")), "errors.forbidden");
    assert.equal(result.errorKey, "errors.forbidden");
  });

  test("a null payload is null data, not an error", async () => {
    // "The read answered, and the answer was nothing" is not a refusal, and drawing it as one
    // would replace an empty report with a sentence apologising for it.
    const result = await streamed(Promise.resolve({ data: null }));
    assert.deepEqual(result, { data: null, errorKey: null });
  });
});
