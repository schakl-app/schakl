/**
 * The leave module's two member tables open on the people who still work here.
 *
 * #405 folded Instellingen → Gebruikers; the team balances on /leave/team and the entitlement
 * table under Instellingen → Verlof kept listing every colleague who ever worked here, flat,
 * between the ones who still do. Same rules, pinned the same way (`users-roster-fold.test.ts`):
 * the split is on the derived `is_active`, the fold is closed by default and absent when nobody
 * has left, the strip carries the count, and there is exactly **one** row markup per table
 * rendered by both halves — a second copy grown for the fold is how a former colleague quietly
 * loses the vacation split, the ⋯ or the badge that says they left.
 *
 * Source text rather than a rendered component: there is no vitest here, and the invariant is
 * about the shape of the template (docs/WORKFLOW.md).
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const MESSAGES = join(here, "../../../../messages");
const FOLD = join(here, "../../src/lib/modules/leave/DeactivatedMembersRow.svelte");
const BADGE = join(here, "../../src/lib/modules/leave/DeactivatedBadge.svelte");

const en = JSON.parse(readFileSync(join(MESSAGES, "en.json"), "utf8")) as Record<string, string>;
const nl = JSON.parse(readFileSync(join(MESSAGES, "nl.json"), "utf8")) as Record<string, string>;

const TABLES = [
  {
    name: "the team balances (/leave/team)",
    path: join(here, "../../src/routes/(app)/leave/team/+page.svelte"),
    snippet: "rosterRow",
    // What must survive inside the fold: the vacation split and the employment ⋯.
    keeps: ["toggleRow(member.user_id)", "employmentMenuItems(member, openEmployment"],
  },
  {
    name: "the entitlement table (Instellingen → Verlof)",
    path: join(here, "../../src/routes/(app)/settings/leave/+page.svelte"),
    snippet: "entitlementRow",
    keeps: ["openMember(member)"],
  },
] as const;

for (const table of TABLES) {
  const source = readFileSync(table.path, "utf8");
  const open = `{#snippet ${table.snippet}(`;
  const snippetStart = source.indexOf(open);
  const snippetEnd = source.indexOf("{/snippet}", snippetStart);
  const rowSnippet = source.slice(snippetStart, snippetEnd);

  describe(`${table.name} splits on the derived active bit`, () => {
    test("both halves come from `is_active`", () => {
      // `MemberLookup.is_active` is `user.is_active and deactivated_at is None`
      // (app/core/members.py): one bit answering for both reasons an account is off.
      assert.match(
        source,
        /const activeMembers = \$derived\(data\.members\.filter\(\(m\) => m\.is_active\)\)/,
      );
      assert.match(
        source,
        /const inactiveMembers = \$derived\(data\.members\.filter\(\(m\) => !m\.is_active\)\)/,
      );
    });

    test("the fold is closed by default", () => {
      assert.match(source, /let showDeactivated = \$state\(false\)/);
    });

    test("no new request and no new query parameter — the split is client-side", () => {
      // §9: the endpoint's default stays everything; the screen picks the narrowing.
      assert.ok(!/members\/lookup\?/.test(source), "the table must not ask for a subset");
    });
  });

  describe(`${table.name} says how much it is hiding, or is not there`, () => {
    test("the fold row renders only when somebody is deactivated", () => {
      assert.ok(
        source.includes("{#if inactiveMembers.length > 0}"),
        "an empty 'Gedeactiveerd (0)' strip is a heading over a negative sentence",
      );
      assert.ok(source.includes("<DeactivatedMembersRow"));
      assert.match(source, /count=\{inactiveMembers\.length\}/);
    });

    test("the hidden rows are drawn only once the fold is open", () => {
      const fold = source.slice(source.indexOf("<DeactivatedMembersRow"));
      assert.ok(fold.includes("{#if showDeactivated}"));
      assert.match(fold, /\{#each inactiveMembers as member \(member\.user_id\)\}/);
    });
  });

  describe(`${table.name} draws both halves from one row`, () => {
    test("there is exactly one row markup block", () => {
      assert.equal(source.split(open).length - 1, 1, "a second copy of the row is the bug");
      assert.ok(snippetStart !== -1 && snippetEnd > snippetStart, `${table.snippet} not found`);
    });

    test("both lists render that one snippet", () => {
      const renders = source.split(`{@render ${table.snippet}(member)}`).length - 1;
      assert.equal(renders, 2, "the active list and the fold must render the same row");
      assert.match(source, /\{#each activeMembers as member \(member\.user_id\)\}/);
    });

    test("the row says on itself that the person left", () => {
      // The badge lives on the row, not on the section: a row copied out of the fold by a later
      // change must still read as a former colleague.
      assert.ok(rowSnippet.includes("{#if !member.is_active}"));
      assert.ok(rowSnippet.includes("<DeactivatedBadge />"));
    });

    test("what the row offered before the fold, it still offers inside it", () => {
      for (const kept of table.keeps) {
        assert.ok(rowSnippet.includes(kept), `${kept} must stay inside the row snippet`);
      }
    });
  });
}

describe("the fold row and the badge speak in both catalogs", () => {
  const fold = readFileSync(FOLD, "utf8");
  const badge = readFileSync(BADGE, "utf8");

  test("the strip carries the count", () => {
    assert.match(fold, /t\("leave\.team\.deactivated_section", \{ count \}\)/);
    assert.match(fold, /aria-expanded=\{expanded\}/);
    for (const [locale, catalog] of [
      ["en", en],
      ["nl", nl],
    ] as const) {
      const text = catalog["leave.team.deactivated_section"];
      assert.ok(text, `leave.team.deactivated_section missing from ${locale}.json`);
      assert.ok(text.includes("{count}"), `${locale} must interpolate the count`);
      assert.ok(catalog["leave.team.deactivated_hint"], `hint missing from ${locale}.json`);
    }
  });

  test("the badge uses the members' own word for the state", () => {
    // `members.status.inactive` is what every picker's "Gedeactiveerd" heading already says
    // ($lib/core/members.ts) — the leave module must not invent a second word for it.
    assert.ok(badge.includes('t("members.status.inactive")'));
    assert.ok(badge.includes('t("leave.team.deactivated_hint")'));
  });
});
