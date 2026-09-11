/**
 * The delivery matrix posts one JSON field, and two things about that field lost data quietly.
 *
 * An in-app row re-posts the `digest_time` it was loaded with, which the API serialises as
 * "HH:MM:SS"; the parser accepted only the picker's "HH:MM", so every stored time became `null`
 * on the first save. And the field is empty until the form has hydrated — a submit before that
 * must decode to "nothing to save", so the action can refuse it rather than write the
 * server-rendered snapshot and report success.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { parseMatrixPayload } from "../../src/lib/modules/notifications/prefs.server.ts";

describe("parseMatrixPayload", () => {
  test("an empty field (the un-hydrated form) is nothing to save, never an empty matrix", () => {
    assert.equal(parseMatrixPayload(""), null);
    assert.equal(parseMatrixPayload(null), null);
    assert.equal(parseMatrixPayload("not json"), null);
  });

  test("a time keeps its value whether it came from the picker or from the API", () => {
    const body = parseMatrixPayload(
      JSON.stringify({
        events: [
          {
            event_type: "task.commented",
            digest: "weekly",
            digest_time: "08:00:00",
            digest_weekday: 2,
          },
          { event_type: "task.assigned", digest: "daily", digest_time: "09:30" },
        ],
        general: { due_soon_days: 5, quiet_hours_start: "22:00:00", quiet_hours_end: "07:00" },
        email: { digest_time: "08:15:00", digest_weekday: 4 },
      }),
    );
    assert.ok(body);
    assert.deepEqual(
      body.events.map((row) => [row.event_type, row.digest_time, row.digest_weekday]),
      [
        ["task.commented", "08:00", 2],
        ["task.assigned", "09:30", null],
      ],
    );
    assert.deepEqual(body.general, {
      due_soon_days: 5,
      quiet_hours_start: "22:00",
      quiet_hours_end: "07:00",
    });
    assert.deepEqual(body.email, { digest_time: "08:15", digest_weekday: 4 });
  });

  test("a value that is not a time is dropped rather than guessed", () => {
    const body = parseMatrixPayload(
      JSON.stringify({ events: [], general: { quiet_hours_start: "8", quiet_hours_end: "eight" } }),
    );
    assert.ok(body);
    assert.deepEqual(body.general, {
      due_soon_days: null,
      quiet_hours_start: null,
      quiet_hours_end: null,
    });
  });
});
