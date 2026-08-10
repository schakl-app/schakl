/**
 * `canWriteTask` must mirror `TaskService._ensure_task_writable` exactly (issue #19, #12).
 *
 * This is the half of the permission model that a base-key check silently gets wrong, and it
 * is invisible on screen: `tasks.task.write:own` is what the seeded `member` role holds, so
 * `can(user, "tasks.task.write")` answers `true` for that member on *every* task in the list —
 * their own and their forty colleagues'. Every complete-toggle, checklist tick and ⋯ → Bewerken
 * then rendered, and the API 403'd on use. Both versions draw a perfectly ordinary checkbox,
 * which is why this needs a test rather than a screen.
 *
 * The Python side is `apps/api/tests/test_rbac_scopes.py`; these are the same cases.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { canWriteTask } from "../../src/lib/modules/tasks/permissions.ts";

const ME = "11111111-1111-1111-1111-111111111111";
const COLLEAGUE = "22222222-2222-2222-2222-222222222222";

const member = { id: ME, permissions: ["tasks.task.read", "tasks.task.write:own"] };
const manager = { id: ME, permissions: ["tasks.task.read", "tasks.task.write:any"] };
const reader = { id: ME, permissions: ["tasks.task.read"] };
const owner = { id: ME, permissions: ["*"] };

describe("canWriteTask", () => {
  test("`:own` is the assignee, and nobody else's task", () => {
    assert.equal(canWriteTask(member, { assignee_user_id: ME }), true);
    assert.equal(canWriteTask(member, { assignee_user_id: COLLEAGUE }), false);
  });

  test("an unassigned task is nobody's, so only `:any` reaches it", () => {
    assert.equal(canWriteTask(member, { assignee_user_id: null }), false);
    assert.equal(canWriteTask(member, {}), false);
    assert.equal(canWriteTask(manager, { assignee_user_id: null }), true);
  });

  test("`:any` writes anything", () => {
    assert.equal(canWriteTask(manager, { assignee_user_id: ME }), true);
    assert.equal(canWriteTask(manager, { assignee_user_id: COLLEAGUE }), true);
  });

  test("the owner's wildcard writes anything", () => {
    assert.equal(canWriteTask(owner, { assignee_user_id: COLLEAGUE }), true);
  });

  test("reading a task is not writing it — not even your own", () => {
    assert.equal(canWriteTask(reader, { assignee_user_id: ME }), false);
  });

  test("no viewer, no task: both answer no rather than throwing", () => {
    assert.equal(canWriteTask(null, { assignee_user_id: ME }), false);
    assert.equal(canWriteTask(undefined, { assignee_user_id: ME }), false);
    assert.equal(canWriteTask(member, null), false);
    // A viewer whose id never arrived must not match an assignee id that is also absent.
    assert.equal(canWriteTask({ permissions: ["tasks.task.write:own"] }, {}), false);
  });
});
