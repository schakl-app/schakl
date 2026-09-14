/**
 * A task in the interaction link pickers says whose it is while no client has been picked.
 *
 * The review dialog on a pending mail opens with no client, so its task list spans every
 * client the agency has — and "Maandrapportage nakijken" on four clients' work is four
 * identical rows until one of them is opened. The resolver reads the two lookups the dialog
 * already loaded: a task's own client, or its project's when it only names a project.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { taskClientName } from "../../src/lib/modules/interactions/taskclient.ts";

const SOURCES = {
  companies: [
    { value: "nova", label: "Nova Fietsen" },
    { value: "bakkerij", label: "Bakkerij Van Loon" },
  ],
  projects: [
    { value: "webshop", company_id: "nova" },
    { value: "intern", company_id: null },
  ],
};

describe("taskClientName", () => {
  const clientOf = taskClientName(SOURCES);

  test("names the task's own client", () => {
    assert.equal(clientOf({ company_id: "bakkerij", project_id: null }), "Bakkerij Van Loon");
  });

  test("reaches the client through the project when the task only names a project", () => {
    assert.equal(clientOf({ company_id: null, project_id: "webshop" }), "Nova Fietsen");
  });

  test("prefers the task's own client over its project's", () => {
    assert.equal(clientOf({ company_id: "bakkerij", project_id: "webshop" }), "Bakkerij Van Loon");
  });

  test("names nothing for an unlinked task, a clientless project or an unknown client", () => {
    assert.equal(clientOf({ company_id: null, project_id: null }), undefined);
    assert.equal(clientOf({ company_id: null, project_id: "intern" }), undefined);
    assert.equal(clientOf({ company_id: "outside-the-lookup", project_id: null }), undefined);
  });
});
