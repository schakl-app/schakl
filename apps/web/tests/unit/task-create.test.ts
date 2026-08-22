/**
 * A task is named by the person making it (#391).
 *
 * `Nieuwe taak` used to post the row before anyone had typed anything: a placeholder title
 * ("Naamloze taak"), `unnamed: true` (#350), a due date of nothing and an assignee it chose
 * itself. One click and a closed tab left real work on the board, in the client's Taken panel
 * and in the export. The dialog in front of it is the fix, and this is the half of the fix that
 * a browser cannot show you — a body builder is invisible in review, and "the title is the
 * caller's" is exactly the kind of thing a later refactor re-introduces a default for.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { taskCreateBody } from "../../src/lib/modules/tasks/create.ts";

const ME = "11111111-1111-1111-1111-111111111111";
const JAN = "22222222-2222-2222-2222-222222222222";

/** The posted fields, without a DOM. */
function posted(fields: Record<string, string>) {
  return { get: (name: string) => (name in fields ? fields[name] : null) };
}

describe("taskCreateBody", () => {
  test("the title is the caller's, and nothing else is invented", () => {
    const body = taskCreateBody(posted({ title: "Productfeed opschonen" }));
    assert.equal(body?.title, "Productfeed opschonen");
    // The point of the issue: no placeholder, and nothing marks the row as unnamed.
    assert.ok(body && !("unnamed" in body));
  });

  test("no title is a refusal, not a placeholder", () => {
    assert.equal(taskCreateBody(posted({})), null);
    assert.equal(taskCreateBody(posted({ title: "   " })), null);
  });

  test("whitespace around a real title is trimmed, not counted", () => {
    assert.equal(
      taskCreateBody(posted({ title: "  Offerte nabellen " }))?.title,
      "Offerte nabellen",
    );
  });

  test("the deadline and the client the dialog asked for reach the body", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: "2026-09-01", company_id: "c1" }));
    assert.equal(body?.due_date, "2026-09-01");
    assert.equal(body?.company_id, "c1");
  });

  test("an empty date or client is null, never the empty string", () => {
    const body = taskCreateBody(posted({ title: "x", due_date: "", company_id: "" }));
    assert.equal(body?.due_date, null);
    assert.equal(body?.company_id, null);
    assert.equal(body?.project_id, null);
  });

  test("a surface that pins the project outranks the form", () => {
    const body = taskCreateBody(posted({ title: "x", project_id: "posted" }), {
      projectId: "the-project",
    });
    assert.equal(body?.project_id, "the-project");
  });

  test("the roster the picker posted is what is sent — including nobody", () => {
    const chosen = taskCreateBody(
      posted({ title: "x", assignees: JSON.stringify([{ user_id: JAN, is_primary: true }]) }),
      { fallbackAssigneeUserId: ME },
    );
    assert.deepEqual(chosen?.assignees, [{ user_id: JAN, is_primary: true }]);
    assert.ok(!("assignee_user_id" in chosen!));

    // `[]` is a decision ("nobody"), so the fallback must not overrule it.
    const nobody = taskCreateBody(posted({ title: "x", assignees: "[]" }), {
      fallbackAssigneeUserId: ME,
    });
    assert.deepEqual(nobody?.assignees, []);
  });

  test("no roster at all falls back to the creator, which is what the picker-less org gets", () => {
    const body = taskCreateBody(posted({ title: "x" }), { fallbackAssigneeUserId: ME });
    assert.equal(body?.assignee_user_id, ME);
    assert.ok(!("assignees" in body!));
  });

  test("no roster and no fallback assigns nobody rather than guessing", () => {
    assert.equal(taskCreateBody(posted({ title: "x" }))?.assignee_user_id, null);
  });

  test("the status is left to the org's default (#62)", () => {
    const body = taskCreateBody(posted({ title: "x" }));
    assert.ok(body && !("status" in body));
    assert.equal(body?.priority, "normal");
  });
});
