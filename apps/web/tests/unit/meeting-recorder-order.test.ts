/**
 * A meeting row exists only for a capture that is already running, and the three timers that
 * decide "is this recording still alive" are ordered.
 *
 * The incident these pin: a colleague recorded a three-hour meeting on a phone and not one byte
 * reached the server. The row had been created *before* the microphone was acquired, the capture
 * never really began, and nothing — not the screen, not the poller, not the reaper — disagreed
 * with a row that said `recording`. Every rule below is a line that reads as correct in review
 * and is wrong only in its ordering, which is exactly what a text assertion can hold and a
 * browser cannot show you.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

const read = (path: string) => readFileSync(new URL(`../../src/${path}`, import.meta.url), "utf8");
const recorder = read("lib/modules/meetings/recorder.svelte.ts");
const upload = read("lib/modules/meetings/upload.ts");
const recordPage = read("routes/(app)/meetings/new/+page.svelte");
const detailPage = read("routes/(app)/meetings/[id]/+page.svelte");

/** The number a `const NAME = <n> * <n> …;` declaration evaluates to. */
function constant(source: string, name: string): number {
  const match = source.match(new RegExp(`${name}\\s*=\\s*([0-9_ */]+);`));
  assert.ok(match, `${name} is declared as a plain arithmetic constant`);
  const value = Number(
    match[1]
      .replaceAll("_", "")
      .split("*")
      .reduce((acc, part) => acc * Number(part.trim()), 1),
  );
  assert.ok(Number.isFinite(value), `${name} evaluates to a number`);
  return value;
}

describe("the capture is the precondition, not the consequence", () => {
  test("the record page arms the microphone before it creates the row", () => {
    const start = recordPage.indexOf("async function startRecording()");
    assert.notEqual(start, -1);
    const fn = recordPage.slice(start, recordPage.indexOf("\n  }", start));
    const armed = fn.indexOf("recorder.arm(");
    const created = fn.indexOf("createMeeting()");
    assert.ok(armed !== -1, "startRecording arms the capture");
    assert.ok(created !== -1, "startRecording creates the meeting");
    assert.ok(
      armed < created,
      "the microphone is acquired first: a row is only ever created for a capture that is live",
    );
  });

  test("a capture with nowhere to put it releases the microphone", () => {
    const start = recordPage.indexOf("async function startRecording()");
    const fn = recordPage.slice(start, recordPage.indexOf("\n  }", start));
    const failed = fn.indexOf("if (!id)");
    const aborted = fn.indexOf("recorder.abort()");
    const began = fn.indexOf("recorder.begin(");
    assert.ok(failed !== -1, "the create can fail");
    assert.ok(
      aborted > failed && aborted < began,
      "an armed recorder whose row was never created lets the microphone go, before it begins",
    );
  });

  test("begin() refuses unless the recorder was armed", () => {
    assert.match(
      recorder,
      /begin\(meetingId: string\): Promise<boolean> \{\s*\n\s*if \(this\.state !== "armed"/,
      "begin() is reachable only from the armed state",
    );
  });
});

describe("the recording says it is alive within seconds, not within a minute", () => {
  test("the first piece is asked for well before the first timeslice", () => {
    const first = constant(recorder, "FIRST_CHUNK_MS");
    const slice = constant(recorder, "CHUNK_MS");
    assert.ok(
      first < slice,
      `the first piece (${first}ms) is asked for before the timeslice would deliver one (${slice}ms)`,
    );
    assert.ok(first <= 15_000, "seconds, not most of a minute: this is the head start");
    assert.match(recorder, /requestData\(\)/, "it is asked for with requestData(), not a re-start");
  });

  test("what is safe is measured, never multiplied by the piece length", () => {
    assert.ok(
      !/uploaded \* 60/.test(recordPage),
      "'opgeslagen tot' reads the measured boundary; uploaded * 60 lies once a piece is short",
    );
    assert.match(recordPage, /recorder\.savedSeconds/);
  });
});

describe("the three timers that decide a recorder is gone are ordered", () => {
  test("the upload gives up before the screen offers to finish without it", () => {
    const budget = constant(upload, "UPLOAD_RETRY_BUDGET_MS");
    const stalled = constant(detailPage, "STALLED_AFTER_MS");
    assert.ok(
      budget < stalled,
      `a live recorder retries for ${budget}ms, so offering to process at ${stalled}ms would ` +
        "queue a meeting still being recorded and 409 every remaining piece",
    );
  });

  test("the screen offers it before the server does it unasked", () => {
    const stalled = constant(detailPage, "STALLED_AFTER_MS");
    const jobs = readFileSync(
      new URL("../../../api/app/modules/meetings/jobs.py", import.meta.url),
      "utf8",
    );
    const server = Number(jobs.match(/RECORDING_STALE_AFTER_MINUTES = (\d+)/)?.[1]);
    assert.ok(Number.isFinite(server), "the server states its own threshold");
    assert.ok(
      stalled <= server * 60_000,
      `a person may end a dead recording (${stalled}ms) before the server ends it for them ` +
        `(${server * 60_000}ms)`,
    );
  });
});

describe("a capture that dies is noticed", () => {
  test("the wake lock is taken again on every return to visibility", () => {
    assert.match(
      recorder,
      /visibilitychange/,
      "the browser releases a screen wake lock when the page hides and hands it back to nobody",
    );
    assert.match(recorder, /#holdScreen\(\)/);
  });

  test("a torn-down recorder and a lost microphone both end the recording", () => {
    assert.ok(
      (recorder.match(/this\.captureLost = true;/g) ?? []).length >= 2,
      "both the frozen-tab case and the microphone-taken-away case are detected",
    );
    assert.match(recordPage, /meetings\.record\.capture_lost/, "and the screen says so");
  });
});
