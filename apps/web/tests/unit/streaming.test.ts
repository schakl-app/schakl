/**
 * `headStart` (`$lib/core/streaming`): a read that answers inside the budget ships as its value,
 * one that does not is handed back as the same promise — never awaited past the budget, never
 * flattened, and never allowed to turn a rejection into a thrown load.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { headStart, isPending, settledNow } from "../../src/lib/core/streaming.ts";

const after = <T>(ms: number, value: T): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), ms));

describe("headStart", () => {
  test("what answers inside the budget is a value, what does not is the same promise", async () => {
    const slow = after(200, "late");
    const started = await headStart({ fast: after(5, "early"), slow }, 60);
    assert.equal(started.fast, "early");
    assert.equal(started.slow, slow);
    assert.equal(await started.slow, "late");
  });

  test("a null answer is an answer, not a pending read", async () => {
    const started = await headStart({ empty: Promise.resolve(null) }, 60);
    assert.equal(started.empty, null);
    assert.equal(isPending(started.empty), false);
    assert.equal(settledNow(started.empty), null);
  });

  test("the budget ends the wait as soon as every read has answered", async () => {
    const t0 = Date.now();
    await headStart({ a: after(5, 1), b: after(10, 2) }, 5000);
    assert.ok(Date.now() - t0 < 1000, "did not sit out the whole budget");
  });

  test("a rejection stays on its promise instead of failing the load", async () => {
    const failing = Promise.reject(new Error("api down"));
    failing.catch(() => undefined); // the load's own handling; keeps node quiet here
    const started = await headStart({ failing, ok: Promise.resolve("fine") }, 60);
    assert.equal(started.ok, "fine");
    assert.equal(started.failing, failing);
  });

  test("isPending tells a promise from its answer", () => {
    assert.equal(isPending(Promise.resolve(1)), true);
    assert.equal(isPending({ data: 1 }), false);
    assert.equal(isPending(null), false);
    assert.deepEqual(settledNow({ data: 1 }), { data: 1 });
    assert.equal(settledNow(Promise.resolve(1)), null);
  });
});
