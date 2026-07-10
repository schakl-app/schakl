/**
 * `hasPermission` must mirror the API's `PermissionSet.has` exactly (issue #19).
 *
 * If the two ever disagree, the UI offers a button the API refuses — or, worse, hides one it
 * would have allowed. The Python side is covered by `apps/api/tests/test_rbac_model.py`; these
 * are the same cases, in the same order.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { can, canAccessSettings, hasPermission } from "../../src/lib/core/permissions.ts";

describe("hasPermission", () => {
  test("a scoped permission is stored suffixed, so a bare check means 'at some scope'", () => {
    const member = ["time.entry.write:own", "tasks.task.create"];
    assert.equal(hasPermission(member, "time.entry.write"), true);
    assert.equal(hasPermission(member, "time.entry.write", "own"), true);
    // …but not the broad grant.
    assert.equal(hasPermission(member, "time.entry.write", "any"), false);
  });

  test("a genuinely unscoped permission answers every scope", () => {
    const member = ["tasks.task.create"];
    assert.equal(hasPermission(member, "tasks.task.create"), true);
    assert.equal(hasPermission(member, "tasks.task.create", "own"), true);
    assert.equal(hasPermission(member, "tasks.task.create", "any"), true);
  });

  test("`:any` satisfies a check for `:own`", () => {
    const manager = ["time.entry.write:any"];
    assert.equal(hasPermission(manager, "time.entry.write"), true);
    assert.equal(hasPermission(manager, "time.entry.write", "own"), true);
    assert.equal(hasPermission(manager, "time.entry.write", "any"), true);
  });

  test("the owner's wildcard satisfies everything", () => {
    assert.equal(hasPermission(["*"], "anything.at.all", "any"), true);
  });

  test("no permissions means no", () => {
    assert.equal(hasPermission([], "time.entry.write"), false);
    assert.equal(hasPermission(undefined, "time.entry.write"), false);
    // A near-miss must not match: the suffix is part of the string, not a prefix rule.
    assert.equal(hasPermission(["time.entry.writer:own"], "time.entry.write"), false);
  });
});

describe("can", () => {
  test("a null user holds nothing", () => {
    assert.equal(can(null, "companies.company.read"), false);
    assert.equal(can(undefined, "companies.company.read"), false);
    assert.equal(can({}, "companies.company.read"), false);
  });

  test("reads through to the user's permission list", () => {
    const member = { permissions: ["companies.company.read", "tasks.task.write:own"] };
    assert.equal(can(member, "companies.company.read"), true);
    assert.equal(can(member, "companies.company.write"), false);
    assert.equal(can(member, "tasks.task.write"), true);
    assert.equal(can(member, "tasks.task.write", "any"), false);
  });
});

describe("canAccessSettings", () => {
  test("one screen is enough to make Instellingen findable", () => {
    assert.equal(canAccessSettings(["settings.branding.write"]), true);
    assert.equal(canAccessSettings(["members.member.read"]), true);
    assert.equal(canAccessSettings(["*"]), true);
  });

  test("a plain member sees no Instellingen", () => {
    const member = [
      "companies.company.read",
      "tasks.task.write:own",
      "time.entry.write:own",
      "dashboard.prefs.write",
    ];
    assert.equal(canAccessSettings(member), false);
  });
});
