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
 * never transcribed on its own and why the order is the file.
 *
 * The **tab** source is `getDisplayMedia` with audio: what a Meet, Teams or Zoom call in the
 * browser plays, mixed with the microphone through an `AudioContext` so both sides of the
 * conversation land in one track. Chrome and Edge offer tab audio; Safari answers the call with
 * a video track and no audio, so the control is offered only where `supportsTabAudio()` says so.
 * The video track is kept alive (unrecorded) for the length of the capture: stopping it is what
 * ends a display capture in some browsers, and this recorder must not learn that in production.
 *
 * Everything here is capability-detected after mount, never inferred from a user agent.
 */
import { blobToBase64 } from "$lib/core/voice";
import { micErrorKey } from "$lib/core/voice";

export type MeetingRecorderState = "idle" | "starting" | "recording" | "stopping" | "finished";
export type CaptureSource = "microphone" | "tab";

/** One upload a minute: a crash loses at most this much, and a two-hour meeting is 120 rows. */
export const CHUNK_MS = 60_000;
/** Opus at 32 kbit/s: transparent for speech, ~14 MB an hour, well inside every provider's cap. */
const AUDIO_BITS_PER_SECOND = 32_000;
/** The hard stop: a forgotten recording is a bill and a privacy problem, and four hours is not a meeting. */
export const MAX_MEETING_MS = 4 * 3600_000;
/** An upload cut into pieces this size — under the API's per-piece cap with room for base64. */
export const UPLOAD_PIECE_BYTES = 4 * 1024 * 1024;
const RETRY_DELAYS_MS = [1_000, 3_000, 8_000];

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

/** One piece to the API. Returns an i18n error key, or null. */
export async function uploadChunk(
  meetingId: string,
  seq: number,
  blob: Blob,
): Promise<string | null> {
  const audio = await blobToBase64(blob);
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(`/api/v1/meetings/${meetingId}/chunks`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ seq, audio }),
      });
      if (res.ok) return null;
      // A refusal the API means (413, 409, 422) will not change on retry; a 5xx might.
      if (res.status < 500) {
        const payload = await res.json().catch(() => null);
        return payload?.error?.message ?? "errors.server";
      }
    } catch {
      // network: retry below
    }
    if (attempt >= RETRY_DELAYS_MS.length) return "meetings.record.upload_failed";
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
  }
}

export class MeetingRecorder {
  state = $state<MeetingRecorderState>("idle");
  error = $state<string | null>(null);
  /** Seconds elapsed, shown beside the stop button. */
  elapsed = $state(0);
  /** Pieces uploaded so far and pieces still in flight — "opgeslagen tot 12:00". */
  uploaded = $state(0);
  pending = $state(0);
  /** A piece that could not be uploaded after every retry; the recording goes on, and the
   *  finish waits until it is retried. */
  uploadError = $state<string | null>(null);
  stoppedAtLimit = $state(false);
  source = $state<CaptureSource>("microphone");

  #meetingId: string | null = null;
  #recorder: MediaRecorder | null = null;
  #streams: MediaStream[] = [];
  #audioContext: AudioContext | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  #stopTimer: ReturnType<typeof setTimeout> | null = null;
  #seq = 0;
  #queue: Promise<void> = Promise.resolve();
  #failed: { seq: number; blob: Blob }[] = [];
  #wakeLock: { release: () => Promise<void> } | null = null;
  #finished: ((ok: boolean) => void) | null = null;

  get active(): boolean {
    return this.state === "starting" || this.state === "recording" || this.state === "stopping";
  }

  /** Begin capturing for `meetingId`. Resolves when the capture has ended and every piece is
   *  uploaded (`true`), or when it was aborted / a piece is still failing (`false`). */
  async start(meetingId: string, source: CaptureSource): Promise<boolean> {
    if (this.active) return false;
    this.#meetingId = meetingId;
    this.source = source;
    this.error = null;
    this.uploadError = null;
    this.stoppedAtLimit = false;
    this.state = "starting";
    this.#seq = 0;
    this.uploaded = 0;
    this.pending = 0;
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
    this.#recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.#enqueue(event.data);
    };
    const finished = new Promise<boolean>((resolve) => (this.#finished = resolve));
    this.#recorder.onstop = () => void this.#finish();
    this.#recorder.start(CHUNK_MS);
    this.state = "recording";
    this.elapsed = 0;
    this.#timer = setInterval(() => (this.elapsed += 1), 1000);
    this.#stopTimer = setTimeout(() => {
      this.stoppedAtLimit = true;
      this.stop();
    }, MAX_MEETING_MS);
    void this.#holdScreen();
    return finished;
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
    for (const piece of again) this.#enqueue(piece.blob, piece.seq);
    await this.#queue;
    if (this.state === "finished" && !this.#failed.length) this.#finished?.(true);
  }

  async #acquire(source: CaptureSource): Promise<MediaStream> {
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.#streams.push(mic);
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

  #enqueue(blob: Blob, seq: number = this.#seq++): void {
    const meetingId = this.#meetingId;
    if (!meetingId) return;
    this.pending += 1;
    this.#queue = this.#queue.then(async () => {
      const error = await uploadChunk(meetingId, seq, blob);
      this.pending -= 1;
      if (error) {
        this.#failed.push({ seq, blob });
        this.uploadError = error;
      } else {
        this.uploaded += 1;
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

  async #holdScreen(): Promise<void> {
    try {
      const wakeLock = (
        navigator as Navigator & {
          wakeLock?: { request: (t: string) => Promise<{ release: () => Promise<void> }> };
        }
      ).wakeLock;
      this.#wakeLock = (await wakeLock?.request("screen")) ?? null;
    } catch {
      // not granted or not supported: the recording still runs while the screen is on
    }
  }

  #clearTimers(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    if (this.#stopTimer !== null) clearTimeout(this.#stopTimer);
    this.#timer = null;
    this.#stopTimer = null;
  }

  #release(): void {
    this.#clearTimers();
    for (const stream of this.#streams) for (const track of stream.getTracks()) track.stop();
    this.#streams = [];
    void this.#audioContext?.close().catch(() => undefined);
    this.#audioContext = null;
    this.#recorder = null;
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
