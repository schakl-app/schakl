/**
 * The review slide-over taking in a run that landed while it was open (`adoptRun`, #327).
 *
 * Found by driving it against a real key: type a note while schakl reads the mail, watch the
 * strip say "aangevuld", press "Toon wat is aangevuld" — and the button went away while the
 * note stayed exactly as typed. The unforced reveal had moved the description's baseline to
 * the server text it had just declined to show, so the forced one had nothing left to add.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { adoptRun, type ReviewFields } from "../../src/lib/modules/tasks/review.ts";

const blank: ReviewFields = { title: "Homepage", description: "", project_id: "", due_date: "" };
const landed: ReviewFields = {
  title: "Homepage",
  description: "Nieuwe homepage online voor vrijdag; teksten staan in Drive.",
  project_id: "proj-1",
  due_date: "2026-09-25",
};

describe("adoptRun", () => {
  test("a form nobody touched takes every field the run wrote", () => {
    const out = adoptRun(blank, blank, landed, false);
    assert.deepEqual(out.form, landed);
    assert.deepEqual(out.baseline, landed);
    assert.equal(out.shown, true);
    assert.equal(out.remountDescription, true);
  });

  test("a description being typed in is kept, the button is offered, and its baseline stays", () => {
    const typing = { ...blank, description: "Eigen notitie: prijslijst opvragen." };
    const out = adoptRun(typing, blank, landed, false);
    assert.equal(out.form.description, typing.description);
    assert.equal(out.shown, false, "something the run wrote is not on screen");
    assert.equal(out.remountDescription, false, "never remount over a reader's cursor");
    // The other fields were untouched and are adopted regardless.
    assert.equal(out.form.project_id, "proj-1");
    assert.equal(out.form.due_date, "2026-09-25");
    // The load-bearing half: the description's baseline is *not* advanced to the server's
    // text, or the forced reveal below compares that text against itself and adds nothing.
    assert.equal(out.baseline.description, "");
    assert.equal(out.baseline.project_id, "proj-1");
  });

  test("pressing the button then merges the run's notes under the reader's words", () => {
    const typing = { ...blank, description: "Eigen notitie: prijslijst opvragen." };
    const first = adoptRun(typing, blank, landed, false);
    const second = adoptRun(
      { ...first.form, description: typing.description },
      first.baseline,
      landed,
      true,
    );
    assert.equal(
      second.form.description,
      `${typing.description}\n\n${landed.description}`,
      "the reader's words first, then what the run added",
    );
    assert.equal(second.shown, true);
    assert.equal(second.remountDescription, true);
    assert.equal(second.baseline.description, landed.description);
  });

  test("what the run appended under an existing description is what gets merged, without the rule", () => {
    const existing = { ...blank, description: "Al eerder genoteerd." };
    const typing = { ...existing, description: "Al eerder genoteerd. En nog iets." };
    const appended = { ...landed, description: `Al eerder genoteerd.\n\n---\n\nUit de mail: X.` };
    const out = adoptRun(typing, existing, appended, true);
    assert.equal(out.form.description, "Al eerder genoteerd. En nog iets.\n\nUit de mail: X.");
  });

  test("a field the reader changed is theirs whether or not the run wrote it", () => {
    const edited = { ...blank, title: "Homepage (spoed)", due_date: "2026-09-10" };
    const out = adoptRun(edited, blank, landed, false);
    assert.equal(out.form.title, "Homepage (spoed)");
    assert.equal(out.form.due_date, "2026-09-10");
    assert.equal(out.form.description, landed.description, "untouched, so adopted");
    assert.equal(out.shown, true);
  });

  test("a run that wrote nothing new leaves a touched description alone and offers no button", () => {
    const typing = { ...blank, description: "Eigen notitie." };
    const out = adoptRun(typing, blank, { ...landed, description: "" }, false);
    assert.equal(out.form.description, "Eigen notitie.");
    assert.equal(out.shown, true);
  });
});
