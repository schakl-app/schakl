/**
 * Microphone capture for dictated input (#246).
 *
 * The browser only *records*; the tenant's own speech provider does the transcription, so the
 * audio goes to the service the organisation already chose and nowhere else. That is the whole
 * reason this is not the Web Speech API: Chrome's implementation ships audio to Google, which
 * is not a decision a self-hosted agency platform should make on its users' behalf.
 *
 * Everything here is capability-detected after mount and never inferred from a user agent.
 */
import { blobToBase64 } from "./encode";

export type RecorderState = "idle" | "recording" | "working";

/** A clip longer than this is a monologue, not a time entry. Also a cost ceiling. */
export const MAX_RECORD_MS = 60_000;

/** MIME types worth asking for, best first. The API sniffs the container either way. */
const PREFERRED_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

export function recordingSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    // getUserMedia is only exposed in a secure context, so an http:// host has no microphone
    // no matter what the browser supports.
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

function pickMimeType(): string | undefined {
  for (const type of PREFERRED_TYPES) {
    if (MediaRecorder.isTypeSupported?.(type)) return type;
  }
  return undefined;
}

/** Map a getUserMedia rejection to a message key. Anything unnamed is the generic failure. */
export function micErrorKey(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") return "voice.error_denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "voice.error_no_mic";
  return "voice.error_failed";
}

export class Recorder {
  state = $state<RecorderState>("idle");
  error = $state<string | null>(null);
  /** Seconds elapsed, so the control can show that it is still listening. */
  elapsed = $state(0);

  #recorder: MediaRecorder | null = null;
  #stream: MediaStream | null = null;
  #chunks: Blob[] = [];
  #timer: ReturnType<typeof setInterval> | null = null;
  #stopTimer: ReturnType<typeof setTimeout> | null = null;
  #settle: ((clip: string | null) => void) | null = null;

  get active(): boolean {
    return this.state !== "idle";
  }

  /** Begin capturing. Resolves with base64 audio when `stop()` runs, or null if aborted. */
  async start(): Promise<string | null> {
    if (this.active) return null;
    this.error = null;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this.error = micErrorKey(err);
      return null;
    }
    this.#stream = stream;
    this.#chunks = [];
    const mimeType = pickMimeType();
    this.#recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    this.#recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.#chunks.push(event.data);
    };

    const finished = new Promise<string | null>((resolve) => (this.#settle = resolve));
    this.#recorder.onstop = () => {
      void this.#finish();
    };
    this.#recorder.start();
    this.state = "recording";
    this.elapsed = 0;
    this.#timer = setInterval(() => (this.elapsed += 1), 1000);
    // A forgotten microphone is both a privacy problem and a bill, so the cap is enforced
    // here rather than trusted to the user noticing.
    this.#stopTimer = setTimeout(() => this.stop(), MAX_RECORD_MS);
    return finished;
  }

  /** Stop and hand back what was captured. */
  stop(): void {
    if (this.state !== "recording" || !this.#recorder) return;
    this.state = "working";
    this.#clearTimers();
    this.#recorder.stop();
  }

  /** Stop and discard — Escape, or leaving the page. */
  abort(): void {
    if (!this.active) {
      this.#release();
      return;
    }
    this.#chunks = [];
    this.#clearTimers();
    try {
      this.#recorder?.stop();
    } catch {
      // already stopped; the release below is what matters
    }
    this.state = "idle";
    this.#release();
    this.#settle?.(null);
    this.#settle = null;
  }

  async #finish(): Promise<void> {
    const chunks = this.#chunks;
    this.#chunks = [];
    this.#release();
    const settle = this.#settle;
    this.#settle = null;
    this.state = "idle";
    if (!chunks.length) {
      settle?.(null);
      return;
    }
    const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
    try {
      settle?.(await blobToBase64(blob));
    } catch {
      this.error = "voice.error_failed";
      settle?.(null);
    }
  }

  #clearTimers(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    if (this.#stopTimer !== null) clearTimeout(this.#stopTimer);
    this.#timer = null;
    this.#stopTimer = null;
  }

  /**
   * Release the microphone. Not optional: a live track keeps the browser's recording
   * indicator lit after the user thinks they stopped, which reads as being spied on.
   */
  #release(): void {
    this.#clearTimers();
    for (const track of this.#stream?.getTracks() ?? []) track.stop();
    this.#stream = null;
    this.#recorder = null;
  }
}
