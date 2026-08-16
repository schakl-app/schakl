/**
 * The hour-entry form's three pickers, and what each one answers about the other two.
 *
 * Every rule here is invisible on screen. A task picked without its project saves an entry that
 * reaches no budget and no invoice and looks completely ordinary on the timesheet; a project
 * kept after the client under it changed sits in a field the dropdown has already stopped
 * offering. Both cost weeks later, in a report nobody can reconcile.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  pickCompany,
  pickProject,
  pickTask,
  scopeIndex,
} from "../../src/lib/modules/time/scope.ts";

const PROJECTS = [
  { id: "p1", company_id: "c1" },
  { id: "p2", company_id: "c2" },
  // A project the agency runs for itself: attached to no client.
  { id: "p3", company_id: null },
];

const TASKS = [
  // The ordinary shape: the task hangs off a project and carries no client of its own.
  { id: "t1", project_id: "p1", company_id: null },
  { id: "t2", project_id: "p2", company_id: null },
  // A task filed straight under a client, with no project.
  { id: "t3", project_id: null, company_id: "c1" },
  // Attached to neither.
  { id: "t4", project_id: null, company_id: null },
];

const index = scopeIndex(PROJECTS, TASKS);
const empty = { companyId: "", projectId: "", taskId: "" };

describe("scopeIndex", () => {
  test("a task's client comes from its project when it carries none itself", () => {
    assert.equal(index.companyOfTask(index.task("t1")), "c1");
  });

  test("a task's own client wins over the one it could be resolved through", () => {
    const own = scopeIndex(PROJECTS, [{ id: "t1", project_id: "p2", company_id: "c1" }]);
    assert.equal(own.companyOfTask(own.task("t1")), "c1");
  });

  test("nothing to resolve resolves to nothing, never to a guess", () => {
    assert.equal(index.companyOfTask(index.task("t4")), "");
    assert.equal(index.companyOfProject("p3"), "");
    assert.equal(index.companyOfProject("unknown-to-the-lookup"), "");
    assert.equal(index.companyOfTask(undefined), "");
  });
});

describe("pickTask", () => {
  test("picking a task fills the project it sits on and that project's client", () => {
    assert.deepEqual(pickTask("t1", empty, index), {
      companyId: "c1",
      projectId: "p1",
      taskId: "t1",
    });
  });

  test("a task filed straight under a client fills the client and asks for no project", () => {
    assert.deepEqual(pickTask("t3", empty, index), {
      companyId: "c1",
      projectId: "",
      taskId: "t3",
    });
  });

  test("a task attached to nothing keeps whatever context is already there", () => {
    const current = { companyId: "c2", projectId: "p2", taskId: "" };
    assert.deepEqual(pickTask("t4", current, index), { ...current, taskId: "t4" });
  });

  test("it overrules a client the form was already showing", () => {
    // The whole point: the task is the most specific thing said so far, so it wins. A form left
    // on the last entry's client must not quietly file this task under that one.
    const current = { companyId: "c2", projectId: "p2", taskId: "" };
    assert.deepEqual(pickTask("t1", current, index), {
      companyId: "c1",
      projectId: "p1",
      taskId: "t1",
    });
  });

  test("clearing the task is not a pick and moves nothing else", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.deepEqual(pickTask("", current, index), { ...current, taskId: "" });
  });
});

describe("pickProject", () => {
  test("picking a project fills its client", () => {
    assert.deepEqual(pickProject("p2", empty, index), {
      companyId: "c2",
      projectId: "p2",
      taskId: "",
    });
  });

  test("a client-less project leaves the client alone rather than emptying it", () => {
    const current = { companyId: "c1", projectId: "", taskId: "" };
    assert.equal(pickProject("p3", current, index).companyId, "c1");
  });

  test("a task belonging to a different project goes with it", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.equal(pickProject("p2", current, index).taskId, "");
  });

  test("a task attached to no project is contradicted by none, so it stays", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t4" };
    assert.equal(pickProject("p2", current, index).taskId, "t4");
  });

  test("clearing the project keeps the task: no project narrows nothing", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.deepEqual(pickProject("", current, index), { ...current, projectId: "" });
  });
});

describe("pickCompany", () => {
  test("a project and task belonging to another client are dropped together", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.deepEqual(pickCompany("c2", current, index), {
      companyId: "c2",
      projectId: "",
      taskId: "",
    });
  });

  test("re-picking the same client contradicts nothing", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.deepEqual(pickCompany("c1", current, index), current);
  });

  test("a project and a task attached to nobody survive every client", () => {
    const current = { companyId: "c1", projectId: "p3", taskId: "t4" };
    assert.deepEqual(pickCompany("c2", current, index), { ...current, companyId: "c2" });
  });

  test("a task under the new client survives its project being dropped", () => {
    // `t3` is filed under c1 directly, so switching to c1 keeps it while p2 goes.
    const current = { companyId: "c2", projectId: "p2", taskId: "t3" };
    assert.deepEqual(pickCompany("c1", current, index), {
      companyId: "c1",
      projectId: "",
      taskId: "t3",
    });
  });

  test("clearing the client narrows nothing, so it clears nothing", () => {
    const current = { companyId: "c1", projectId: "p1", taskId: "t1" };
    assert.deepEqual(pickCompany("", current, index), { ...current, companyId: "" });
  });
});
