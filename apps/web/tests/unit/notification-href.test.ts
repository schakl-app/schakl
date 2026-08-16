/**
 * A notification opens the record it is about (issue #16, #164).
 *
 * The inbox's whole job is getting you from a sentence to the thing that sentence describes, so
 * a row with no destination is a row that failed. Two of them shipped that way — `interaction`
 * only ever linked to whatever a note *hung on* (`null` for one that hangs on nothing) and
 * `snelstart_account` was never given a destination at all — and neither is visible in review,
 * because a missing link renders as ordinary text.
 *
 * So this pins the map, and pins it against the *API's own* entity vocabulary: the sweep at the
 * bottom is the only part that catches the eighth entity type somebody adds.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { notificationHref } from "../../src/lib/modules/notifications/href.ts";

/**
 * Every `entity_type` the API's fan-out can write — `app/modules/notifications/events.py`.
 * Kept as a literal rather than derived, so adding one here is a deliberate act with a
 * destination beside it.
 */
const API_ENTITY_TYPES = [
  "task",
  "project",
  "company",
  "leave_request",
  "timesheet",
  "interaction",
  "snelstart_account",
] as const;

const ID = "11111111-2222-3333-4444-555555555555";

const at = (entity_type: string, event_type: string, payload: Record<string, unknown> = {}) =>
  notificationHref({ entity_type, event_type, entity_id: ID, payload });

describe("notificationHref", () => {
  test("a record opens its own detail page", () => {
    assert.equal(at("task", "task.assigned"), `/tasks/${ID}`);
    assert.equal(at("project", "project.assigned"), `/projects/${ID}`);
    assert.equal(at("company", "company.created"), `/companies/${ID}`);
  });

  test("leave splits by who is being asked", () => {
    // Waiting on *you* → the team review, where approve/deny is one click away.
    assert.equal(at("leave_request", "leave.requested"), `/leave/team?request=${ID}`);
    // A decision about *your* request → your own list.
    assert.equal(at("leave_request", "leave.approved"), `/leave?request=${ID}`);
    assert.equal(at("leave_request", "leave.rejected"), `/leave?request=${ID}`);
  });

  test("a timesheet reminder opens the week it is about, not this one", () => {
    assert.equal(
      at("timesheet", "time.timesheet_reminder", { week_start: "2026-08-03" }),
      "/time?week=2026-08-03",
    );
  });

  test("…and falls back to the timesheet when the event names no week", () => {
    // `time.entry_approved` carries a count and minutes, never a week.
    assert.equal(at("timesheet", "time.entry_approved", { count: 3, minutes: 90 }), "/time");
  });

  test("a pending email opens that message, with the review queue behind it", () => {
    assert.equal(
      at("interaction", "interactions.email_pending"),
      `/interactions?status=pending&interaction=${ID}`,
    );
  });

  test("a mention opens the note itself, past the 'mijn' default that would hide it", () => {
    // The note was written by the colleague who mentioned you, so it is not one of *your*
    // contact moments — the list's own default filter would answer an empty page.
    assert.equal(
      at("interaction", "interactions.mentioned", { task_id: "not-used-any-more" }),
      `/interactions?owner=all&interaction=${ID}`,
    );
  });

  test("a failed accounting sync opens the connection that failed", () => {
    assert.equal(at("snelstart_account", "snelstart.sync.failed"), "/settings/snelstart");
  });

  test("every entity type the API can write has somewhere to go", () => {
    for (const entity of API_ENTITY_TYPES) {
      assert.ok(
        at(entity, `${entity}.something`),
        `${entity} has no destination — it would render as plain text in the inbox`,
      );
    }
  });

  test("an entity type nobody has taught it is a quiet row, never a broken link", () => {
    // Never `""`: an empty href navigates to the page you are already on, which reads as a
    // control that refuses (docs/UX.md, #253). The inbox draws a `<span>` for `null`.
    assert.equal(at("website", "websites.something"), null);
  });
});
