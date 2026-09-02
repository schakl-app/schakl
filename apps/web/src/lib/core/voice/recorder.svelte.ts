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

/** A time entry is one clause, said with a breath around it. Two minutes; also a cost ceiling. */
export const MAX_RECORD_MS = 120_000;

/**
 * What a dictated *task* gets (#382). A time entry is one clause; a task carries its steps,
 * and the first cap (120 s) cut people off mid-checklist — a real task dictation runs to five
 * minutes. Still a hard cap for #246's other reason, which has nothing to do with cost: a
 * forgotten microphone keeps the browser's recording indicator lit, which reads as being spied
 * on. The cap is *shown* (`VoiceButton` prints elapsed against maximum) and reaching it is
 * *said* (`stoppedAtLimit`), because a recording that ends on its own with no sentence about
 * why looks exactly like the feature being broken.
 */
export const MAX_TASK_RECORD_MS = 300_000;

/** A spoken message for the assistant: a question, or a whole instruction with its details. */
export const MAX_CHAT_RECORD_MS = 300_000;

/**
 * Opus at 32 kbit/s is transparent for speech and keeps five minutes at ~1.2 MB — which is
 * what lets a long dictation fit the API's cap (`core/ai/audio.py`) and the web server's body
 * limit (`BODY_SIZE_LIMIT`, `apps/web/Dockerfile`) with room to spare. A hint the browser may
 * ignore; the caps on the far side are sized for that too.
 */
const AUDIO_BITS_PER_SECOND = 32_000;

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
  /** The hard stop for one capture. A constructor argument rather than a module constant
   *  because the right length is a property of what is being dictated (#382), and a host that
   *  needs longer must not be able to get it by editing a number every other host shares. */
  readonly maxMs: number;

  constructor(maxMs: number = MAX_RECORD_MS) {
    this.maxMs = maxMs;
  }

  state = $state<RecorderState>("idle");
  error = $state<string | null>(null);
  /** Seconds elapsed, so the control can show that it is still listening. */
  elapsed = $state(0);
  /** True when the last capture was ended by the cap rather than by the user — the host says
   *  so, because a recording that stops on its own and says nothing reads as broken. */
  stoppedAtLimit = $state(false);

  #recorder: MediaRecorder | null = null;
  #stream: MediaStream | null = null;
  #chunks: Blob[] = [];
  #timer: ReturnType<typeof setInterval> | null = null;
  #stopTimer: ReturnType<typeof setTimeout> | null = null;
  #settle: ((clip: string | null) => void) | null = null;
  /**
   * Bumped by every `start()` and every `abort()`, so a capture that was awaiting the
   * permission prompt can tell it has been superseded by the time it resolves. A boolean
   * cannot answer that question: the stream arrives *after* the state has already moved on.
   */
  #generation = 0;

  get active(): boolean {
    return this.state !== "idle";
  }

  /** Begin capturing. Resolves with base64 audio when `stop()` runs, or null if aborted. */
  async start(): Promise<string | null> {
    if (this.active) return null;
    this.error = null;
    // Enter a non-idle state *before* awaiting the permission prompt. `active` is the only
    // re-entry guard, and leaving it false across this await lets a second click — ordinary
    // while a permission dialog is up — open a second stream: the first one's tracks are then
    // overwritten by the assignments below and never stopped, so the browser's recording
    // indicator stays lit after the user believes they stopped, and the first call's promise
    // never settles. Same check-then-act-across-an-await shape as the encode race, one await
    // earlier.
    this.state = "working";
    this.stoppedAtLimit = false;
    const generation = ++this.#generation;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      if (generation === this.#generation) {
        this.error = micErrorKey(err);
        this.state = "idle";
      }
      return null;
    }
    if (generation !== this.#generation) {
      // Aborted while the prompt was up — leaving the page, or Escape. This stream belongs to
      // nobody now, and only this closure still holds a reference to release it.
      for (const track of stream.getTracks()) track.stop();
      return null;
    }
    this.#stream = stream;
    this.#chunks = [];
    const mimeType = pickMimeType();
    try {
      this.#recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: AUDIO_BITS_PER_SECOND,
      });
    } catch {
      // `isTypeSupported` is advisory and may be missing entirely, so construction can still
      // fail. Hand the microphone back and return to idle: the state entered above is only
      // safe because every path out of here leaves it.
      this.error = "voice.error_failed";
      this.#release();
      this.state = "idle";
      return null;
    }
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
    this.#stopTimer = setTimeout(() => {
      this.stoppedAtLimit = true;
      this.stop();
    }, this.maxMs);
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
    // Before the early return: a capture still waiting on the permission prompt has not
    // reached `recording` yet, and this is the only thing that tells it to let go.
    this.#generation++;
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
    // Stay in `working` across the encode, not just up to it. Returning to `idle` first would
    // let a second `start()` past the `active` guard while the first clip is still encoding —
    // two recordings racing each other's promises — and would make the button's spinner dead
    // code, since the only state it renders would never be observable.
    try {
      if (!chunks.length) {
        settle?.(null);
        return;
      }
      const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
      settle?.(await blobToBase64(blob));
    } catch {
      this.error = "voice.error_failed";
      settle?.(null);
    } finally {
      this.state = "idle";
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
