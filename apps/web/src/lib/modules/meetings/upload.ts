/**
 * One piece of a meeting recording to the API, and the rule for what happens when it does not
 * land: the piece is retried in the background for as long as a redeploy or a network blip can
 * plausibly last, and only then handed back as failed.
 *
 * A meeting is recorded a minute at a time (`recorder.svelte.ts`) and the person recording it is
 * in the meeting, not watching the screen — so a refusal that needs a button pressed is a piece
 * that stays unsaved until the meeting ends. The two outages worth surviving without a person
 * are a **rolling redeploy** (the API rolls start-first on two replicas, but a request can still
 * meet a task being retired, or the edge stack restarting) and a **connection that drops for a
 * few minutes** (a phone leaving wifi). Both end on their own, and neither is over in the
 * twelve seconds the first version of this retried for.
 *
 * So a piece is retried with a capped backoff until `UPLOAD_RETRY_BUDGET_MS` has been spent
 * waiting, and the caller is told about every attempt (`onRetry`) so the screen can say
 * "reconnecting" in amber rather than "failed" in red. What does not change: a refusal the API
 * *means* (a 4xx — the row is not recording any more, the piece is too large) returns at once,
 * because no retry would change it. Pieces are held in memory meanwhile, in order, and the API
 * replaces a piece it already has (idempotent per sequence number), so a retry after a lost
 * response cannot double a minute of the meeting.
 *
 * Plain TypeScript, no runes: this is the half of the recorder that a unit test can run under
 * node (`tests/unit/meeting-upload-retry.test.ts`).
 */
// Relative on purpose: `node --test` runs this file without SvelteKit's `$lib` alias.
import { blobToBase64 } from "../../core/voice/encode.ts";

/** How long one piece keeps retrying before it is handed back as failed. */
export const UPLOAD_RETRY_BUDGET_MS = 10 * 60_000;
/** The backoff ladder; the last step repeats until the budget is spent. */
export const UPLOAD_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000];

/** The wait before attempt `attempt + 1` (attempt 0 is the first try). */
export function retryDelayMs(attempt: number): number {
  return UPLOAD_RETRY_DELAYS_MS[Math.min(attempt, UPLOAD_RETRY_DELAYS_MS.length - 1)];
}

/**
 * A status worth trying again on: the edge or a task in the middle of a restart (5xx, including
 * Cloudflare's 52x), a timeout, or a rate limit. Everything else under 500 is an answer.
 */
export function retryableStatus(status: number): boolean {
  return status >= 500 || status === 408 || status === 429;
}

export interface UploadDeps {
  fetch: typeof globalThis.fetch;
  /** The piece as base64; injected because Node has no `FileReader`. */
  encode: (blob: Blob) => Promise<string>;
  /** Wait this long; injected so a test does not. */
  sleep: (ms: number) => Promise<void>;
  /** Ceiling on the total time spent waiting between attempts. */
  budgetMs: number;
  /** Told before every wait: which attempt just failed and how long the next wait is. */
  onRetry?: (attempt: number, waitMs: number) => void;
  /**
   * Which recorder session the piece belongs to and whether it opens that session (carries
   * the container header). A recording that was never interrupted is session 0 throughout,
   * and says nothing — the wire stays what it was.
   */
  origin?: { session: number; head: boolean };
}

const defaultDeps: UploadDeps = {
  fetch: (...args) => globalThis.fetch(...args),
  encode: blobToBase64,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  budgetMs: UPLOAD_RETRY_BUDGET_MS,
};

/** One piece to the API. Returns an i18n error key, or null once it landed. */
export async function uploadChunk(
  meetingId: string,
  seq: number,
  blob: Blob,
  deps: Partial<UploadDeps> = {},
): Promise<string | null> {
  const { fetch, encode, sleep, budgetMs, onRetry, origin } = { ...defaultDeps, ...deps };
  const audio = await encode(blob);
  const session = origin?.session ?? 0;
  const body = JSON.stringify(
    session > 0 ? { seq, audio, session, head: origin?.head ?? false } : { seq, audio },
  );
  let waited = 0;
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(`/api/v1/meetings/${meetingId}/chunks`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      });
      if (res.ok) return null;
      if (!retryableStatus(res.status)) {
        const payload = await res.json().catch(() => null);
        return payload?.error?.message ?? "errors.server";
      }
    } catch {
      // network: retry below
    }
    const wait = retryDelayMs(attempt);
    if (waited + wait > budgetMs) return "meetings.record.upload_failed";
    onRetry?.(attempt, wait);
    waited += wait;
    await sleep(wait);
  }
}
