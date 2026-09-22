/**
 * Recording a meeting in the browser: the microphone, or a tab's audio mixed with it, uploaded
 * a minute at a time while it runs.
 *
 * The dictation recorder (`core/voice/recorder.svelte.ts`) keeps a clip in memory and hands it
 * over at the end, which is right for a two-minute clause and wrong for a two-hour meeting: a
 * tab that crashes at 1:58 would lose everything, and a single upload at the end would be the
 * one request every hop in front of the API has to admit whole. So this one asks
 * `MediaRecorder` for a blob every `CHUNK_MS` and uploads each as it lands — one piece a minute,
 * in order, retried, and the API folds them (`app/modules/meetings/pipeline.py`). Only the first
 * piece carries the container header; the rest are continuations of it, which is why a piece is
 * never transcribed on its own and why the order is the file. A piece that does not land is
 * retried in the background for minutes, not seconds (`upload.ts`): the person is in the meeting,
 * a redeploy or a dropped connection ends on its own, and `retrying` is what the screen says in
 * the meantime — the capture never stops for it.
 *
 * The **tab** source is `getDisplayMedia` with audio: what a Meet, Teams or Zoom call in the
 * browser plays, mixed with the microphone through an `AudioContext` so both sides of the
 * conversation land in one track. Chrome and Edge offer tab audio; Safari answers the call with
 * a video track and no audio, so the control is offered only where `supportsTabAudio()` says so.
 * The video track is kept alive (unrecorded) for the length of the capture: stopping it is what
 * ends a display capture in some browsers, and this recorder must not learn that in production.
 *
 * Three rules came out of the first three-hour meeting recorded on a phone, of which not one
 * byte reached the server. **A row is only ever created for a capture that is already
 * running** — `arm()` acquires the microphone and `begin()` starts it, so the caller creates
 * the meeting between the two and a capture that never begins leaves nothing behind (before
 * this, the row came first and a permission prompt nobody answered left a meeting stamped
 * `recording` that nothing would ever end). **The first piece is asked for after seconds**
 * (`FIRST_CHUNK_MS`), because everything that kills a recording kills it at the start, and
 * until a piece lands there is no recording on the server at all. And **a capture that dies is
 * noticed**: the screen wake lock is re-taken on every return to visibility (the browser
 * releases it the moment the page is hidden, and hands it back to nobody), a `MediaRecorder`
 * the browser tore down while the tab was frozen ends the recording rather than letting a
 * timer go on lying, and a microphone the OS takes back does the same.
 *
 * Everything here is capability-detected after mount, never inferred from a user agent.
 */
import { micErrorKey } from "$lib/core/voice";

import { uploadChunk } from "./upload";

export { uploadChunk } from "./upload";

export type MeetingRecorderState =
  | "idle"
  | "starting"
  /** The microphone is live and the recorder is built, but nothing is being captured yet:
   *  the caller is creating the meeting row. */
  | "armed"
  | "recording"
  | "stopping"
  | "finished";
export type CaptureSource = "microphone" | "tab";

/** One upload a minute: a crash loses at most this much, and a two-hour meeting is 120 rows. */
export const CHUNK_MS = 60_000;
/**
 * The first piece is asked for after seconds, not after a minute (`requestData()`, which
 * hands over what is buffered without ending the capture).
 *
 * A minute is the right size for a *piece* and the wrong size for the *first* one. Everything
 * that can go wrong with a recording goes wrong at the start — the phone locks, the tab is
 * backgrounded and frozen, the person walks into the meeting room — and until the first piece
 * lands there is nothing on the server at all: not a byte to transcribe, and no evidence the
 * recording was ever really running. With this, a recording that dies in its first minute is a
 * short recording rather than no recording, and the server can tell a live recorder from a
 * dead one within seconds of the start rather than within a minute of it.
 */
export const FIRST_CHUNK_MS = 5_000;
/** Opus at 32 kbit/s: transparent for speech, ~14 MB an hour, well inside every provider's cap. */
const AUDIO_BITS_PER_SECOND = 32_000;
/** The hard stop: a forgotten recording is a bill and a privacy problem, and four hours is not a meeting. */
export const MAX_MEETING_MS = 4 * 3600_000;
/** An upload cut into pieces this size — under the API's per-piece cap with room for base64. */
export const UPLOAD_PIECE_BYTES = 4 * 1024 * 1024;

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
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

/** Tab audio needs `getDisplayMedia` *and* a browser that hands audio back with it. Safari has
 *  the former and not the latter, and there is no feature test for the latter — so this asks for
 *  the API and lets the capture itself report a stream with no audio track as the honest error. */
export function supportsTabAudio(): boolean {
  return recordingSupported() && Boolean(navigator.mediaDevices?.getDisplayMedia);
}

function pickMimeType(): string | undefined {
  for (const type of PREFERRED_TYPES) {
    if (MediaRecorder.isTypeSupported?.(type)) return type;
  }
  return undefined;
}

export class MeetingRecorder {
  state = $state<MeetingRecorderState>("idle");
  error = $state<string | null>(null);
  /** Seconds elapsed, shown beside the stop button. */
  elapsed = $state(0);
  /** Pieces uploaded so far and pieces still in flight. */
  uploaded = $state(0);
  pending = $state(0);
  /**
   * How much of the recording is on the server, in seconds — "opgeslagen tot 12:00".
   *
   * Measured, never multiplied. The screen used to say `uploaded * 60`, which was true only
   * while every piece was exactly a minute; with the first piece asked for after seconds
   * (`FIRST_CHUNK_MS`) it would claim a whole minute was safe five seconds in. Each piece
   * carries the elapsed second it was cut at, and the queue is serial, so this is simply the
   * boundary of the last piece that landed.
   */
  savedSeconds = $state(0);
  /** A piece that could not be uploaded after every retry; the recording goes on, and the
   *  finish waits until it is retried. */
  uploadError = $state<string | null>(null);
  /** A piece being retried right now: since when, and how many attempts so far. The recording
   *  goes on and nothing is lost; the screen says "reconnecting" rather than "failed". */
  retrying = $state<{ since: number; attempts: number } | null>(null);
  stoppedAtLimit = $state(false);
  /** The capture ended on its own — the tab was frozen and torn down, or the microphone was
   *  taken away (an incoming call). What was uploaded is kept; the screen says why it stopped. */
  captureLost = $state(false);
  source = $state<CaptureSource>("microphone");

  #meetingId: string | null = null;
  #recorder: MediaRecorder | null = null;
  #streams: MediaStream[] = [];
  #audioContext: AudioContext | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  #stopTimer: ReturnType<typeof setTimeout> | null = null;
  #seq = 0;
  #queue: Promise<void> = Promise.resolve();
  #failed: { seq: number; blob: Blob; covers: number }[] = [];
  #wakeLock: { release: () => Promise<void> } | null = null;
  #unwatchScreen: (() => void) | null = null;
  #firstChunkTimer: ReturnType<typeof setTimeout> | null = null;
  #finished: ((ok: boolean) => void) | null = null;

  get active(): boolean {
    return (
      this.state === "starting" ||
      this.state === "armed" ||
      this.state === "recording" ||
      this.state === "stopping"
    );
  }

  /**
   * Acquire the microphone (and the tab, where that is the source) and build the recorder,
   * **without capturing anything yet**. `true` when the capture is live and `begin()` may be
   * called; `false` with `error` set when it is not.
   *
   * This is half of `start()` on purpose, and the seam is the fix for a meeting that existed
   * on the server and nowhere else. The row used to be created first and the microphone asked
   * for second, so every way the capture can fail to begin — a permission prompt nobody
   * answers, a phone that freezes the tab while the prompt is up, a `MediaRecorder` the
   * browser declines to build — left behind a row stamped `recording` that no audio would ever
   * arrive for, that nothing on the server ended, and that the screen went on describing as a
   * recording in progress. Arming first means a row is only ever created for a capture that is
   * already running: no capture, no row, nothing to clean up.
   */
  async arm(source: CaptureSource): Promise<boolean> {
    if (this.active) return false;
    this.#meetingId = null;
    this.source = source;
    this.error = null;
    this.uploadError = null;
    this.retrying = null;
    this.stoppedAtLimit = false;
    this.captureLost = false;
    this.state = "starting";
    this.#seq = 0;
    this.uploaded = 0;
    this.pending = 0;
    this.savedSeconds = 0;
    this.elapsed = 0;
    this.#failed = [];

    let stream: MediaStream;
    try {
      stream = await this.#acquire(source);
    } catch (err) {
      this.error = source === "tab" ? tabErrorKey(err) : micErrorKey(err);
      this.#release();
      this.state = "idle";
      return false;
    }
    const mimeType = pickMimeType();
    try {
      this.#recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: AUDIO_BITS_PER_SECOND,
      });
    } catch {
      this.error = "voice.error_failed";
      this.#release();
      this.state = "idle";
      return false;
    }
    this.state = "armed";
    return true;
  }

  /**
   * Start capturing into `meetingId`. Resolves when the capture has ended and every piece is
   * uploaded (`true`), or when it was aborted / a piece is still failing (`false`).
   *
   * The first piece is asked for after `FIRST_CHUNK_MS` rather than at the end of the first
   * minute, so the server hears from the recording within seconds of it starting.
   */
  begin(meetingId: string): Promise<boolean> {
    if (this.state !== "armed" || !this.#recorder) return Promise.resolve(false);
    const recorder = this.#recorder;
    this.#meetingId = meetingId;
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.#enqueue(event.data);
    };
    const finished = new Promise<boolean>((resolve) => (this.#finished = resolve));
    recorder.onstop = () => void this.#finish();
    recorder.start(CHUNK_MS);
    this.state = "recording";
    this.elapsed = 0;
    this.#timer = setInterval(() => (this.elapsed += 1), 1000);
    this.#firstChunkTimer = setTimeout(() => {
      this.#firstChunkTimer = null;
      // Not an error if the browser declines: the timeslice still delivers a piece a minute.
      try {
        if (recorder.state === "recording") recorder.requestData();
      } catch {
        // the timeslice is the guarantee; this is the head start
      }
    }, FIRST_CHUNK_MS);
    this.#stopTimer = setTimeout(() => {
      this.stoppedAtLimit = true;
      this.stop();
    }, MAX_MEETING_MS);
    this.#watchScreen();
    return finished;
  }

  /** `arm()` then `begin()` — the whole thing, for a caller with a row already in hand. */
  async start(meetingId: string, source: CaptureSource): Promise<boolean> {
    if (!(await this.arm(source))) return false;
    return this.begin(meetingId);
  }

  stop(): void {
    if (this.state !== "recording" || !this.#recorder) return;
    this.state = "stopping";
    this.#clearTimers();
    this.#recorder.stop();
  }

  /** Stop and discard what has not been uploaded; the API row is the caller's to delete. */
  abort(): void {
    if (!this.active) {
      this.#release();
      return;
    }
    this.#clearTimers();
    if (this.#recorder) this.#recorder.ondataavailable = null;
    try {
      this.#recorder?.stop();
    } catch {
      // already stopped
    }
    this.state = "idle";
    this.#release();
    this.#finished?.(false);
    this.#finished = null;
  }

  /** Try the pieces that failed once more; the finish resolves when they land. */
  async retryFailed(): Promise<void> {
    if (!this.#meetingId || !this.#failed.length) return;
    const again = this.#failed;
    this.#failed = [];
    this.uploadError = null;
    for (const piece of again) this.#enqueue(piece.blob, piece.seq, piece.covers);
    await this.#queue;
    if (this.state === "finished" && !this.#failed.length) this.#finished?.(true);
  }

  async #acquire(source: CaptureSource): Promise<MediaStream> {
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.#streams.push(mic);
    // The OS can take the microphone back — an incoming call, another app claiming it. The
    // track ends, `MediaRecorder` goes on producing silence, and nothing would say so.
    mic.getAudioTracks()[0]?.addEventListener("ended", () => {
      if (this.state !== "recording") return;
      this.captureLost = true;
      this.stop();
    });
    if (source === "microphone") return mic;
    const display = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true,
      // Chrome-only hints, ignored elsewhere: offer the whole screen's audio too, and do not
      // list this tab, which would record the recorder.
      ...({ systemAudio: "include", selfBrowserSurface: "exclude" } as object),
    });
    this.#streams.push(display);
    if (display.getAudioTracks().length === 0) {
      throw new NoTabAudioError();
    }
    // The user ending the share from the browser's own bar ends the capture too.
    display.getVideoTracks()[0]?.addEventListener("ended", () => this.stop());
    const context = new AudioContext();
    this.#audioContext = context;
    const destination = context.createMediaStreamDestination();
    context.createMediaStreamSource(mic).connect(destination);
    context.createMediaStreamSource(new MediaStream(display.getAudioTracks())).connect(destination);
    return destination.stream;
  }

  #enqueue(blob: Blob, seq: number = this.#seq++, covers: number = this.elapsed): void {
    const meetingId = this.#meetingId;
    if (!meetingId) return;
    this.pending += 1;
    this.#queue = this.#queue.then(async () => {
      const error = await uploadChunk(meetingId, seq, blob, {
        onRetry: (attempt) => {
          this.retrying = { since: this.retrying?.since ?? Date.now(), attempts: attempt + 1 };
        },
      });
      this.retrying = null;
      this.pending -= 1;
      if (error) {
        this.#failed.push({ seq, blob, covers });
        this.uploadError = error;
      } else {
        this.uploaded += 1;
        this.savedSeconds = Math.max(this.savedSeconds, covers);
      }
    });
  }

  async #finish(): Promise<void> {
    this.#release();
    await this.#queue;
    this.state = "finished";
    if (this.#failed.length) return; // the finish resolves once `retryFailed` clears them
    this.#finished?.(true);
    this.#finished = null;
  }

  /**
   * Keep the screen on for the length of the capture, and **take the lock again every time the
   * page comes back**.
   *
   * A screen wake lock is released by the browser the moment the document is hidden, and it is
   * never handed back on its own. Asking for it once at the start therefore bought exactly one
   * screen-off: the first time the phone locked or the recorder was switched away from, the
   * lock was gone, and from then on the screen slept on its own schedule — which on a phone
   * freezes the tab, stops `MediaRecorder`, and ends the recording without anything saying so.
   * So the lock is re-requested on every return to visibility, for as long as the capture runs.
   */
  #watchScreen(): void {
    const onVisible = () => {
      if (document.visibilityState !== "visible" || this.state !== "recording") return;
      // A capture that did not survive being away is over, whatever the timer says: a frozen
      // tab resumes with a `MediaRecorder` the browser has already torn down, and the elapsed
      // count — which froze with it — would go on ticking as if nothing had happened. Ending
      // it here is what turns "the recording silently stopped" into a recording that stops,
      // says so, and hands over every piece that did land.
      if (this.#recorder && this.#recorder.state !== "recording") {
        this.captureLost = true;
        this.stop();
        return;
      }
      void this.#holdScreen();
    };
    document.addEventListener("visibilitychange", onVisible);
    this.#unwatchScreen = () => document.removeEventListener("visibilitychange", onVisible);
    void this.#holdScreen();
  }

  async #holdScreen(): Promise<void> {
    if (this.#wakeLock) return;
    try {
      const wakeLock = (
        navigator as Navigator & {
          wakeLock?: {
            request: (t: string) => Promise<{
              release: () => Promise<void>;
              addEventListener?: typeof addEventListener;
            }>;
          };
        }
      ).wakeLock;
      const held = (await wakeLock?.request("screen")) ?? null;
      // The browser may drop it without us asking; forget it so the next return re-takes one.
      held?.addEventListener?.("release", () => {
        if (this.#wakeLock === held) this.#wakeLock = null;
      });
      this.#wakeLock = held;
    } catch {
      // not granted or not supported: the recording still runs while the screen is on
    }
  }

  #clearTimers(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    if (this.#stopTimer !== null) clearTimeout(this.#stopTimer);
    if (this.#firstChunkTimer !== null) clearTimeout(this.#firstChunkTimer);
    this.#timer = null;
    this.#stopTimer = null;
    this.#firstChunkTimer = null;
  }

  #release(): void {
    this.#clearTimers();
    for (const stream of this.#streams) for (const track of stream.getTracks()) track.stop();
    this.#streams = [];
    void this.#audioContext?.close().catch(() => undefined);
    this.#audioContext = null;
    this.#recorder = null;
    this.#unwatchScreen?.();
    this.#unwatchScreen = null;
    void this.#wakeLock?.release().catch(() => undefined);
    this.#wakeLock = null;
  }
}

class NoTabAudioError extends Error {
  name = "NoTabAudioError";
}

function tabErrorKey(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NoTabAudioError") return "meetings.record.error_no_tab_audio";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "meetings.record.error_share_denied";
  return "voice.error_failed";
}

/**
 * A file somebody already has, cut into pieces the API takes: the same route as a live
 * recording, so the worker sees one kind of upload however it arrived. Calls `onprogress` with
 * pieces done / pieces total; returns an i18n error key, or null.
 */
export async function uploadFile(
  meetingId: string,
  file: File,
  onprogress: (done: number, total: number) => void,
): Promise<string | null> {
  const total = Math.max(1, Math.ceil(file.size / UPLOAD_PIECE_BYTES));
  for (let seq = 0; seq < total; seq++) {
    const piece = file.slice(seq * UPLOAD_PIECE_BYTES, (seq + 1) * UPLOAD_PIECE_BYTES);
    const error = await uploadChunk(meetingId, seq, piece);
    if (error) return error;
    onprogress(seq + 1, total);
  }
  return null;
}

/** How long an audio file is, read by the browser — the recorder's count for an upload. */
export function fileDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const audio = document.createElement("audio");
    const url = URL.createObjectURL(file);
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    audio.preload = "metadata";
    audio.onloadedmetadata = () =>
      done(Number.isFinite(audio.duration) ? Math.round(audio.duration) : null);
    audio.onerror = () => done(null);
    audio.src = url;
  });
}
