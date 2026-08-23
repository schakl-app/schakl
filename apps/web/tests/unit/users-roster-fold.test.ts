/**
 * The team roster opens on the people who still work here (#405).
 *
 * Every rule here is invisible on a screen with nobody deactivated on it, which is every
 * developer's screen and most tenants' on the day the feature ships. The failure it guards
 * against is not "the fold does not open" — that is obvious the first time anyone tries it —
 * but the quiet one: a second copy of the row markup grown for the fold that drops a badge, an
 * action or the amber hint, so a colleague who left reads as an ordinary member the moment you
 * expand the section. One snippet rendered by both lists is what makes that impossible, so that
 * is what is pinned, along with the two rules the issue is actually about: the heading carries
 * the count, and the section is absent rather than empty when nobody has left.
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
const PAGE = join(here, "../../src/routes/(app)/settings/users/+page.svelte");
const MESSAGES = join(here, "../../../../messages");

const source = readFileSync(PAGE, "utf8");
const en = JSON.parse(readFileSync(join(MESSAGES, "en.json"), "utf8")) as Record<string, string>;
const nl = JSON.parse(readFileSync(join(MESSAGES, "nl.json"), "utf8")) as Record<string, string>;

/** Where the snippet ends and the two lists begin. */
const snippetStart = source.indexOf("{#snippet memberRow(");
const snippetEnd = source.indexOf("{/snippet}", snippetStart);
const rowSnippet = source.slice(snippetStart, snippetEnd);

describe("the roster splits on the derived active bit", () => {
  test("both halves come from `is_active`, which answers for both inactive states", () => {
    // `MemberRead.is_active` is `user.is_active and deactivated_at is None` (app/core/members.py).
    // Splitting on `deactivated_at` alone would leave an instance-disabled account sitting
    // between two working colleagues — the exact row the fold exists to move.
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
    // §9: the endpoint's default stays everything, the screen picks the narrowing. A `?status=`
    // here would reach the pickers, the export and the generated MCP surface.
    assert.ok(!/members\?[^"'`]*status=/.test(source), "the roster must not ask for a subset");
  });
});

describe("the section says how much it is hiding, or is not there", () => {
  test("it renders only when somebody is deactivated", () => {
    assert.ok(
      source.includes("{#if inactiveMembers.length > 0}"),
      "an empty 'Gedeactiveerd (0)' strip is a heading over a negative sentence",
    );
  });

  test("the heading carries the count", () => {
    assert.match(
      source,
      /t\("settings\.users\.deactivated_section", \{ count: inactiveMembers\.length \}\)/,
    );
  });

  test("the key is in both catalogs and both spell the number", () => {
    for (const [locale, catalog] of [
      ["en", en],
      ["nl", nl],
    ] as const) {
      const text = catalog["settings.users.deactivated_section"];
      assert.ok(text, `settings.users.deactivated_section missing from ${locale}.json`);
      assert.ok(
        text.includes("{count}"),
        `${locale} must interpolate the count — a section with no number reads like an empty one`,
      );
    }
  });
});

describe("the fold is about attention, not about capability", () => {
  test("there is exactly one row markup block", () => {
    assert.equal(
      source.split("{#snippet memberRow(").length - 1,
      1,
      "a second copy of the row is how the fold quietly loses a badge or an action",
    );
    assert.ok(snippetStart !== -1 && snippetEnd > snippetStart, "memberRow snippet not found");
  });

  test("both lists render that one snippet", () => {
    const renders = source.split("{@render memberRow(member)}").length - 1;
    assert.equal(renders, 2, "the active list and the fold must render the same row");
    assert.match(source, /\{#each activeMembers as member \(member\.membership_id\)\}/);
    assert.match(source, /\{#each inactiveMembers as member \(member\.membership_id\)\}/);
  });

  test("both inactive states keep their own badge inside that row", () => {
    // `deactivated_at` set → this org took them off the team, reversible here.
    // `is_active` false alone → the instance disabled the account, and the admin cannot fix it
    // from this screen. Moving under the fold must not collapse the two into one amber pill.
    assert.ok(rowSnippet.includes('t("settings.users.inactive")'));
    assert.ok(rowSnippet.includes('t("settings.users.disabled_elsewhere")'));
    assert.ok(rowSnippet.includes('t("settings.users.deactivated_on"'));
    assert.ok(rowSnippet.includes('t("settings.users.disabled_elsewhere_hint")'));
  });

  test("the ⋯ still travels with the row", () => {
    assert.ok(
      rowSnippet.includes("<ActionsMenu items={memberActions(member)} />"),
      "reactivate, bewerken and intrekken must stay available inside the fold",
    );
  });
});
