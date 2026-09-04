/**
 * The two shapes a task is created in, and why neither may drift into the other.
 *
 * A **dialog** create (a picker's inline ＋) is named by the person making it: the control that
 * opened it needs an id back, so it may not navigate, so the title has to be asked for. Nothing
 * about it is invented — "the title is the caller's" is exactly the kind of thing a later
 * refactor re-introduces a default for, and a body builder is invisible in review.
 *
 * A **placeholder** create is `Nieuwe taak` itself: create-then-edit (#230), one click and
 * straight into edit mode on the detail page. It invents all three of the fields the dialog
 * asks for, so each one is pinned here — above all `unnamed` (#350), which is what keeps an
 * abandoned create findable rather than indistinguishable from real work, and the org's own
 * today rather than a `NULL` that would take the task out of every urgency screen (#392).
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { taskCreateBody, taskPlaceholderBody } from "../../src/lib/modules/tasks/create.ts";

//: A deadline is required too (#392), so every body that is *meant* to build carries one —
//: only the tests about the deadline itself leave it out.
const DUE = "2026-09-01";
const ME = "11111111-1111-1111-1111-111111111111";
const JAN = "22222222-2222-2222-2222-222222222222";

/** The posted fields, without a DOM. */
function posted(fields: Record<string, string>) {
  return { get: (name: string) => (name in fields ? fields[name] : null) };
}

//: Somebody is always on a task, so every body that is *meant* to build names someone — here
//: the way every action does it, by handing the caller over as the picker-less fallback. Only
//: the tests about the roster itself leave it out.
const AS_ME = { fallbackAssigneeUserId: ME };

describe("taskCreateBody", () => {
  test("a client contact holds the task alone, with an explicitly empty roster (#453)", () => {
    const body = taskCreateBody(
      posted({
        title: "Foto's aanleveren",
        due_date: DUE,
        assignee_contact_id: JAN,
        assignees: "[]",
      }),
      { fallbackAssigneeUserId: ME },
    );
    assert.equal(body?.assignee_contact_id, JAN);
    assert.deepEqual(body?.assignees, []);
    // Nobody rides along: not the fallback, not a mirrored single id.
    assert.ok(body && !("assignee_user_id" in body));
  });

  test("the title is the caller's, and nothing else is invented", () => {
    const body = taskCreateBody(posted({ title: "Productfeed opschonen", due_date: DUE }), AS_ME);
    assert.equal(body?.title, "Productfeed opschonen");
    // A named row is not an unnamed one: the flag belongs to the placeholder shape alone.
    assert.ok(body && !("unnamed" in body));
  });

  test("no title is a refusal, not a placeholder", () => {
    assert.equal(taskCreateBody(posted({}), AS_ME), null);
    assert.equal(taskCreateBody(posted({ title: "   " }), AS_ME), null);
  });

  test("whitespace around a real title is trimmed, not counted", () => {
    assert.equal(
      taskCreateBody(posted({ title: "  Offerte nabellen ", due_date: DUE }), AS_ME)?.title,
      "Offerte nabellen",
    );
  });

  test("the deadline and the client the dialog asked for reach the body", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "c1" }), AS_ME);
    assert.equal(body?.due_date, DUE);
    assert.equal(body?.company_id, "c1");
  });

  test("no deadline is a refusal too (#392)", () => {
    // A task with no `due_date` is absent from `?due=overdue`, from the Agenda's deadline feed
    // and from both dashboards' overdue counts — invisible to every screen that is about time,
    // which is why it joined the title in front of the row rather than staying optional.
    assert.equal(taskCreateBody(posted({ title: "x" }), AS_ME), null);
    assert.equal(taskCreateBody(posted({ title: "x", due_date: "" }), AS_ME), null);
    assert.equal(taskCreateBody(posted({ title: "x", due_date: "   " }), AS_ME), null);
  });

  test("an empty client is null, never the empty string", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "" }), AS_ME);
    assert.equal(body?.company_id, null);
    assert.equal(body?.project_id, null);
  });

  test("a surface that pins the project outranks the form", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE, project_id: "posted" }), {
      ...AS_ME,
      projectId: "the-project",
    });
    assert.equal(body?.project_id, "the-project");
  });

  test("the roster the picker posted is what is sent", () => {
    const chosen = taskCreateBody(
      posted({
        title: "x",
        due_date: DUE,
        assignees: JSON.stringify([{ user_id: JAN, is_primary: true }]),
      }),
      AS_ME,
    );
    assert.deepEqual(chosen?.assignees, [{ user_id: JAN, is_primary: true }]);
    // The picker's answer, not the fallback: the fallback is for a form with no picker.
    assert.ok(!("assignee_user_id" in chosen!));
  });

  test("a rendered picker that names nobody is a refusal, and the fallback does not paper over it", () => {
    // Somebody is always on a task: with no one on it, it is on no board, in no one's "mijn
    // taken" and in no one's nudges — #392's invisibility, one column over. `[]` used to be sent
    // as a decision ("nobody"); it is the one decision the dialog no longer accepts.
    assert.equal(
      taskCreateBody(posted({ title: "x", due_date: DUE, assignees: "[]" }), AS_ME),
      null,
    );
    assert.equal(taskCreateBody(posted({ title: "x", due_date: DUE, assignees: "[]" })), null);
    // …and a contact *is* somebody, so the same empty roster beside a contact builds (#453).
    const held = taskCreateBody(
      posted({ title: "x", due_date: DUE, assignees: "[]", assignee_contact_id: JAN }),
    );
    assert.equal(held?.assignee_contact_id, JAN);
  });

  test("no roster at all falls back to the creator, which is what the picker-less org gets", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE }), AS_ME);
    assert.equal(body?.assignee_user_id, ME);
    assert.ok(!("assignees" in body!));
  });

  test("no roster and no fallback is a refusal rather than a guess", () => {
    assert.equal(taskCreateBody(posted({ title: "x", due_date: DUE })), null);
  });

  test("the status is left to the org's default (#62)", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE }), AS_ME);
    assert.ok(body && !("status" in body));
    assert.equal(body?.priority, "normal");
  });
});

describe("taskPlaceholderBody", () => {
  const TODAY = "2026-08-31";
  const placeholder = (extra: Record<string, string | null> = {}) =>
    taskPlaceholderBody({ title: "Naamloze taak", today: TODAY, ...extra });

  test("the row says it is unnamed, so an abandoned create stays findable (#350)", () => {
    const body = placeholder();
    assert.equal(body.title, "Naamloze taak");
    assert.equal(body.unnamed, true);
  });

  test("the deadline is the org's today, never absent (#392)", () => {
    // The default #392 wrote down for exactly this path. A `NULL` here would take the task out
    // of `?due=overdue`, the Agenda's deadline feed and both dashboards' overdue counts — while
    // the user is standing on the field, one keystroke from changing it.
    assert.equal(placeholder().due_date, TODAY);
  });

  test("the creator is assigned, and an unknown one is nobody rather than a guess", () => {
    assert.equal(placeholder({ assigneeUserId: ME }).assignee_user_id, ME);
    assert.equal(placeholder().assignee_user_id, null);
    // The roster field is the dialog's; a placeholder never posts one.
    assert.ok(!("assignees" in placeholder({ assigneeUserId: ME })));
  });

  test("an empty client or project is null, never the empty string", () => {
    const body = placeholder({ companyId: "", projectId: "" });
    assert.equal(body.company_id, null);
    assert.equal(body.project_id, null);
    assert.equal(placeholder({ companyId: "c1" }).company_id, "c1");
  });

  test("the status is left to the org's default (#62), like every other create", () => {
    const body = placeholder();
    assert.ok(!("status" in body));
    assert.equal(body.priority, "normal");
    assert.equal(body.requires_interaction, false);
    assert.equal(body.visible_to_client, false);
  });
});
