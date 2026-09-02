/**
 * One clip → its words, for every dictating host (#246, #382, the assistant).
 *
 * Three hosts each carried their own `fetch` and their own reading of the failure, and only
 * one status was read differently by the three: **413**. It is the one status two different
 * layers answer — the API with its own envelope (`errors.ai_audio_too_large`) and the SvelteKit
 * server, *before* the proxy runs, with a plain-text body when the JSON is over adapter-node's
 * `BODY_SIZE_LIMIT`. A caller that only reads the envelope reports the second as a provider
 * failure — which is how a five-minute task dictation came back as "the AI service did not
 * answer" while the real sentence was "that recording is too long". The status is the fact;
 * the body is a detail.
 *
 * The pure half (`transcribeFailureKey`, `formatClock`) is unit-tested without a browser.
 */

export interface TranscribeOutcome {
  /** The words, or null when there are none to use. */
  text: string | null;
  /** An i18n key for `t()`, or null. */
  error: string | null;
  /** The monthly audio budget is spent; the host offers the override, not a retry. */
  budget: boolean;
}

/** Which message a refused transcription gets. Exported for the unit test. */
export function transcribeFailureKey(
  status: number,
  payload: { error?: { code?: string; message?: string } } | null,
): { error: string | null; budget: boolean } {
  if (status === 413) return { error: "errors.ai_audio_too_large", budget: false };
  if (payload?.error?.code === "ai_budget_reached") return { error: null, budget: true };
  return { error: payload?.error?.message ?? "errors.ai_provider_error", budget: false };
}

/** `0:07`, `4:59`, `12:00` — the shape a recording counter reads at a glance. */
export function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

/** POST a base64 clip to one of the `/ai/<host>/transcribe` proxies. */
export async function transcribeClip(
  path: string,
  audio: string,
  language: string,
  overrideBudget = false,
): Promise<TranscribeOutcome> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ audio, language, override_budget: overrideBudget }),
    });
  } catch {
    return { text: null, error: "errors.ai_provider_error", budget: false };
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    return { text: null, ...transcribeFailureKey(res.status, payload) };
  }
  const body = await res.json().catch(() => null);
  const text = String(body?.text ?? "").trim();
  if (!text) return { text: null, error: "voice.error_no_speech", budget: false };
  return { text, error: null, budget: false };
}
