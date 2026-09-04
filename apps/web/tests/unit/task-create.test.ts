/**
 * The one shape a task is created in, and what it refuses.
 *
 * Every "＋ nieuwe taak" — the list's button, a client's page, its Taken panel, a project's
 * to-do list, a picker's inline ＋ — posts through `taskCreateBody`, and the body is named by
 * the person making it: the control that opened the dialog may need an id back, so nothing
 * is invented and nothing is written before it has been asked for. There used to be a second,
 * placeholder-writing shape beside it (`taskPlaceholderBody`, #350's `unnamed` rows); it is
 * gone, and this file is where "no title is a refusal, not a placeholder" and "no client is a
 * refusal, not a task filed under nobody" are asserted without a browser — "the title is the
 * caller's" is exactly the kind of rule a later refactor re-introduces a default for, and a
 * body builder is invisible in review.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { taskCreateBody } from "../../src/lib/modules/tasks/create.ts";

//: A deadline is required too (#392), so every body that is *meant* to build carries one —
//: only the tests about the deadline itself leave it out.
const DUE = "2026-09-01";
const ME = "11111111-1111-1111-1111-111111111111";
const JAN = "22222222-2222-2222-2222-222222222222";
const CLIENT = "33333333-3333-3333-3333-333333333333";

/** The posted fields, without a DOM. */
function posted(fields: Record<string, string>) {
  return { get: (name: string) => (name in fields ? fields[name] : null) };
}

//: Somebody is always on a task, so every body that is *meant* to build names someone — here
//: the way every action does it, by handing the caller over as the picker-less fallback. Only
//: the tests about the roster itself leave it out. And every such body is a client's, so the
//: client rides along the same way; only the tests about the client leave it out.
const AS_ME = { fallbackAssigneeUserId: ME, companyId: CLIENT };

describe("taskCreateBody", () => {
  test("a client contact holds the task alone, with an explicitly empty roster (#453)", () => {
    const body = taskCreateBody(
      posted({
        title: "Foto's aanleveren",
        due_date: DUE,
        assignee_contact_id: JAN,
        assignees: "[]",
      }),
      AS_ME,
    );
    assert.equal(body?.assignee_contact_id, JAN);
    assert.deepEqual(body?.assignees, []);
    // Nobody rides along: not the fallback, not a mirrored single id.
    assert.ok(body && !("assignee_user_id" in body));
  });

  test("the title is the caller's, and nothing else is invented", () => {
    const body = taskCreateBody(posted({ title: "Productfeed opschonen", due_date: DUE }), AS_ME);
    assert.equal(body?.title, "Productfeed opschonen");
    // There is no placeholder shape left to mark a row with.
    assert.ok(body && !("unnamed" in body));
  });

  test("no title is a refusal, not a placeholder", () => {
    assert.equal(taskCreateBody(posted({}), AS_ME), null);
    assert.equal(taskCreateBody(posted({ title: "   ", due_date: DUE }), AS_ME), null);
  });

  test("whitespace around a real title is trimmed, not counted", () => {
    assert.equal(
      taskCreateBody(posted({ title: "  Offerte nabellen ", due_date: DUE }), AS_ME)?.title,
      "Offerte nabellen",
    );
  });

  test("the deadline and the client the dialog asked for reach the body", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "c1" }), {
      fallbackAssigneeUserId: ME,
    });
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

  test("no client is a refusal, not a task filed under nobody", () => {
    // A task with no client is on no client's page, in no client's export and outside every
    // company horizon — the one place the agency's own work cannot be.
    const without = { fallbackAssigneeUserId: ME };
    assert.equal(taskCreateBody(posted({ title: "x", due_date: DUE }), without), null);
    assert.equal(
      taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "" }), without),
      null,
    );
    assert.equal(
      taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "   " }), without),
      null,
    );
  });

  test("a surface that pins the client outranks the form", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE, company_id: "posted" }), {
      fallbackAssigneeUserId: ME,
      companyId: "the-client",
    });
    assert.equal(body?.company_id, "the-client");
  });

  test("a pinned project stands in for the client — the API takes it off the project", () => {
    // A project has exactly one client, so naming the project *is* naming the client; the
    // project's to-do list pins the project and the body carries no client of its own.
    const body = taskCreateBody(posted({ title: "x", due_date: DUE }), {
      fallbackAssigneeUserId: ME,
      projectId: "the-project",
    });
    assert.equal(body?.project_id, "the-project");
    assert.equal(body?.company_id, null);
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
  });

  test("a form with no picker at all falls back to the caller, and only then", () => {
    const fallen = taskCreateBody(posted({ title: "x", due_date: DUE }), AS_ME);
    assert.equal(fallen?.assignee_user_id, ME);
    assert.ok(!("assignees" in fallen!));
    // …and a caller-less picker-less form is refused too: nobody is never an answer.
    assert.equal(
      taskCreateBody(posted({ title: "x", due_date: DUE }), { companyId: CLIENT }),
      null,
    );
  });

  test("the defaults the dialog does not ask for are fixed, not guessed", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: DUE }), AS_ME);
    assert.equal(body?.priority, "normal");
    assert.equal(body?.requires_interaction, false);
    assert.equal(body?.visible_to_client, false);
    // Status is omitted so the API assigns the org's default status (#62).
    assert.ok(body && !("status" in body));
  });
});
