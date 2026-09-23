/**
 * A meeting piece that does not land is retried for minutes, not seconds (`upload.ts`).
 *
 * The first recorder retried three times over twelve seconds and then parked the piece behind a
 * button. A rolling redeploy of the API, or a phone stepping out of wifi, is longer than that
 * and ends on its own — and the person who would press the button is in the meeting. So a
 * transient failure is retried with a capped backoff until the budget is spent, the caller is
 * told about every wait (the screen says "reconnecting"), and only a refusal the API *means*
 * comes back at once. Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  UPLOAD_RETRY_BUDGET_MS,
  UPLOAD_RETRY_DELAYS_MS,
  retryDelayMs,
  retryableStatus,
  uploadChunk,
} from "../../src/lib/modules/meetings/upload.ts";

const piece = new Blob(["audio"]);
const encode = async () => "YXVkaW8=";

/** A fetch that answers from a script of statuses (`0` = network failure). */
function scripted(statuses: number[]) {
  const calls: string[] = [];
  const fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push(String(init?.body ?? ""));
    const status = statuses.shift();
    if (status === undefined) throw new Error("script exhausted");
    if (status === 0) throw new TypeError("Failed to fetch");
    return new Response(status === 200 ? '{"chunks_received":1}' : '{"error":{"message":"x"}}', {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

describe("uploadChunk", () => {
  test("a 5xx during a redeploy is retried until it lands, and every wait is reported", async () => {
    const { fetch, calls } = scripted([503, 0, 502, 200]);
    const waits: number[] = [];
    const retries: number[] = [];
    const error = await uploadChunk("m1", 3, piece, {
      fetch,
      encode,
      sleep: async (ms) => void waits.push(ms),
      budgetMs: UPLOAD_RETRY_BUDGET_MS,
      onRetry: (attempt, wait) => retries.push(attempt, wait),
    });
    assert.equal(error, null);
    assert.equal(calls.length, 4);
    assert.deepEqual(waits, [1_000, 2_000, 4_000]);
    assert.deepEqual(retries, [0, 1_000, 1, 2_000, 2, 4_000]);
    // Every attempt sends the same piece under the same sequence number: the API replaces a
    // piece it already holds, so a retry after a lost response cannot double a minute.
    assert.ok(calls.every((body) => body === calls[0] && body.includes('"seq":3')));
  });

  test("a refusal the API means comes back at once, with its message", async () => {
    const { fetch, calls } = scripted([409]);
    const error = await uploadChunk("m1", 0, piece, {
      fetch,
      encode,
      sleep: async () => assert.fail("a 409 must not be retried"),
    });
    assert.equal(error, "x");
    assert.equal(calls.length, 1);
  });

  test("a piece gives up only once the budget is spent, and says so", async () => {
    const { fetch, calls } = scripted(Array(100).fill(503));
    const waits: number[] = [];
    const error = await uploadChunk("m1", 0, piece, {
      fetch,
      encode,
      sleep: async (ms) => void waits.push(ms),
      budgetMs: 60_000,
    });
    assert.equal(error, "meetings.record.upload_failed");
    const total = waits.reduce((a, b) => a + b, 0);
    assert.ok(total <= 60_000, `waited ${total}ms over a 60s budget`);
    assert.ok(total >= 45_000, `gave up after only ${total}ms`);
    assert.equal(calls.length, waits.length + 1);
  });

  test("the ladder caps at its last step and the default budget is minutes", () => {
    const last = UPLOAD_RETRY_DELAYS_MS[UPLOAD_RETRY_DELAYS_MS.length - 1];
    assert.equal(retryDelayMs(0), UPLOAD_RETRY_DELAYS_MS[0]);
    assert.equal(retryDelayMs(99), last);
    assert.ok(UPLOAD_RETRY_BUDGET_MS >= 5 * 60_000);
    // A redeploy's 5xx and Cloudflare's 52x retry; a validation answer does not.
    assert.ok(retryableStatus(503) && retryableStatus(524) && retryableStatus(429));
    assert.ok(!retryableStatus(413) && !retryableStatus(409) && !retryableStatus(401));
  });
});
