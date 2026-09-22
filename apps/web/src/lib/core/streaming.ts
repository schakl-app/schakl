/**
 * A head start for a streamed read (docs/PERFORMANCE.md, "stream the rest").
 *
 * A `load` that returns a promise streams it behind the shell, which is right for a read that is
 * somebody else's latency and wrong for one that answers in a hundred milliseconds: the shell
 * arrives without it, the section draws a placeholder, the answer lands, and the page re-arranges
 * itself for a wait nobody needed. The marketing tab did that three times over — the tiles, the
 * leads dashboard and the AI Search overview each flipped in on their own clock — on a page
 * whose reads are a Redis hit on every ordinary open.
 *
 * `headStart` waits one short budget for *all* of the reads together. What answered ships in
 * the shell, rendered server-side in its final shape; what did not is returned as the same
 * promise and streams exactly as before. The budget is the most a cold read may delay the
 * shell, so it is short — the point is a warm answer costing no reflow, never a cold one
 * costing a wait.
 */

export type HeadStarted<T extends Record<string, Promise<unknown>>> = {
  [K in keyof T]: Awaited<T[K]> | T[K];
};

/** Whether a load value is still a streaming promise (as opposed to its settled answer). */
export function isPending<T>(value: T | Promise<T>): value is Promise<T> {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { then?: unknown }).then === "function"
  );
}

/** The settled answer of a load value, or `null` while it streams. */
export function settledNow<T>(value: T | Promise<T>): T | null {
  return isPending(value) ? null : value;
}

/**
 * Await `reads` for at most `budgetMs`; each key holds its answer if it arrived in time and its
 * promise otherwise. A rejection is left to the promise — it streams and fails where it would
 * have failed anyway — so a throwing read can never turn the shell into an error page.
 */
export async function headStart<T extends Record<string, Promise<unknown>>>(
  reads: T,
  budgetMs = 300,
): Promise<HeadStarted<T>> {
  const answers = new Map<string, unknown>();
  const settled = Object.entries(reads).map(([key, promise]) =>
    promise.then(
      (value) => {
        answers.set(key, value);
      },
      () => undefined,
    ),
  );
  let timer: ReturnType<typeof setTimeout> | undefined;
  const budget = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, budgetMs);
  });
  await Promise.race([Promise.all(settled), budget]);
  clearTimeout(timer);
  const out: Record<string, unknown> = {};
  for (const [key, promise] of Object.entries(reads)) {
    out[key] = answers.has(key) ? answers.get(key) : promise;
  }
  return out as HeadStarted<T>;
}
