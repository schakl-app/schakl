/**
 * What an hour entry bills, and whether anybody has said so yet (`modules/time/billable.ts`).
 *
 * Every rule here is invisible on screen: the toggle is a real control showing a real value, and
 * the only thing that disagrees with a wrongly-frozen one is the project's own settings page —
 * usually after the hours have been invoiced. The freeze this file pins down is the bug: a draft
 * (#44) writes `billable` on every autosave, so reading its presence as "the person decided"
 * meant one typed word froze the flag for the rest of the day.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { billableSettled, projectBillableDefault } from "../../src/lib/modules/time/billable.ts";

const PROJECTS = [
  { id: "p1", billable_default: true },
  // A project a subscription covers: the retainer already pays for the work (#284).
  { id: "p2", billable_default: false },
  // A lookup that trimmed the field, or a caller that never asked for it.
  { id: "p3" },
];

describe("what a project bills by default", () => {
  test("the project answers", () => {
    assert.equal(projectBillableDefault(PROJECTS, "p1"), true);
    assert.equal(projectBillableDefault(PROJECTS, "p2"), false);
  });

  test("no project, an unknown one, or a row without the field: the platform default", () => {
    assert.equal(projectBillableDefault(PROJECTS, ""), true);
    assert.equal(projectBillableDefault(PROJECTS, null), true);
    assert.equal(projectBillableDefault(PROJECTS, "gone"), true);
    assert.equal(projectBillableDefault(PROJECTS, "p3"), true);
  });
});

describe("whether the person settled it themselves", () => {
  test("no draft, or one that says nothing: the project keeps answering", () => {
    assert.equal(billableSettled(null, PROJECTS), false);
    assert.equal(billableSettled({}, PROJECTS), false);
    assert.equal(billableSettled({ billable: null }, PROJECTS), false);
  });

  test("the flag is the answer wherever the draft carries one", () => {
    assert.equal(billableSettled({ billable: true, billable_touched: true }, PROJECTS), true);
    // The case that was broken: a draft carrying a value nobody chose. Autosave writes
    // `billable` on every keystroke, so this shape is the *ordinary* one, not the exception.
    assert.equal(
      billableSettled({ billable: true, billable_touched: false, project_id: "p1" }, PROJECTS),
      false,
    );
  });

  test("an older draft is read by its own project: a value that differs is a decision", () => {
    // Billable on a project that bills — nothing was decided, so picking another project may
    // still answer.
    assert.equal(billableSettled({ billable: true, project_id: "p1" }, PROJECTS), false);
    // Billable on a retainer project — somebody overruled the default, and switching projects
    // must not take that back.
    assert.equal(billableSettled({ billable: true, project_id: "p2" }, PROJECTS), true);
    assert.equal(billableSettled({ billable: false, project_id: "p2" }, PROJECTS), false);
    assert.equal(billableSettled({ billable: false, project_id: null }, PROJECTS), true);
  });
});
