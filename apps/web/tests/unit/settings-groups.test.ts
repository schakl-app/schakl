/**
 * A settings screen sits in the group its owning module's *kind* puts it in (CLAUDE.md §6a).
 *
 * The rule exists because the previous single group had already drifted: "Communicatie &
 * koppelingen" held Marketing, Rapportage and Meldingen — three things schakl does — beside
 * Google, Cloudflare, Uptime Kuma, OXXA and Mollie, which are five accounts belonging to somebody
 * else. Nothing in the build could notice, because the group was a free-text string typed once per
 * screen and never compared to anything.
 *
 * So it is compared to something now: the `kind` the web module declares, which mirrors the API's
 * `ModuleDescriptor.kind`. The next integration that ships lands in the wrong group exactly once.
 *
 * Screens with no `module` are core seams and are deliberately unconstrained — E-mail and AI read
 * as integrations and sit there, SSO reads as access and stays under Team & toegang.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  SETTINGS_GROUPS,
  SETTINGS_SCREENS,
  groupSettingsScreens,
  matchSettingsScreens,
  type SettingsScreen,
} from "../../src/lib/core/settings-nav.ts";

/**
 * Which modules are integrations, read from the web registry's own declarations rather than
 * retyped here — a second list is a second thing to forget.
 *
 * Importing `$lib/core/registry` would drag Svelte components in under a plain node runner, so the
 * declarations are read as text. The assertion is about the *source of truth*, and the source of
 * truth is the `kind:` each integration's `index.ts` states.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LIB = join(dirname(fileURLToPath(import.meta.url)), "../../src/lib");

function declaredKind(root: string, name: string): string | null {
  try {
    const source = readFileSync(join(LIB, root, name, "index.ts"), "utf8");
    return /kind:\s*"integration"/.test(source) ? "integration" : "module";
  } catch {
    return null;
  }
}

const integrationDirs = readdirSync(join(LIB, "integrations"), { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name);

describe("modules and integrations are separated in Instellingen", () => {
  test("every package under lib/integrations declares kind: integration", () => {
    assert.ok(integrationDirs.length > 0, "no integrations found — the split is gone");
    for (const name of integrationDirs) {
      assert.equal(
        declaredKind("integrations", name),
        "integration",
        `lib/integrations/${name}/index.ts does not declare kind: "integration". ` +
          "A path that does not predict a kind makes both of them noise (CLAUDE.md §6a).",
      );
    }
  });

  test("no package under lib/modules declares kind: integration", () => {
    for (const entry of readdirSync(join(LIB, "modules"), { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const kind = declaredKind("modules", entry.name);
      if (kind === null) continue; // no index.ts: not a self-registering web module
      assert.equal(
        kind,
        "module",
        `lib/modules/${entry.name} declares kind: "integration" — move it to lib/integrations/.`,
      );
    }
  });

  test("an integration's settings screen is in the integrations group, and only there", () => {
    const owned = SETTINGS_SCREENS.filter((screen) => screen.module);
    assert.ok(owned.length > 0);
    for (const screen of owned) {
      const isIntegration = integrationDirs.includes(screen.module!);
      const group = isIntegration ? "integrations" : "modules";
      // A module's screen may legitimately sit in `data` (keuzelijsten), `workspace` or
      // `personal`. Only the two groups this rule is about are constrained against each other:
      // what must never happen is Cloudflare under Modules or Rapportage under Integraties.
      if (screen.group !== "modules" && screen.group !== "integrations") continue;
      assert.equal(
        screen.group,
        group,
        `settings screen '${screen.key}' is owned by ${screen.module} ` +
          `(${isIntegration ? "an integration" : "a module"}) but sits in '${screen.group}'.`,
      );
    }
  });

  test("every screen names a group the registry declares", () => {
    const known = new Set(SETTINGS_GROUPS.map((group) => group.key));
    for (const screen of SETTINGS_SCREENS) {
      assert.ok(known.has(screen.group), `screen '${screen.key}' names unknown group '${screen.group}'`);
    }
  });
});

describe("the index and the rail search the same way", () => {
  // Both render `groupSettingsScreens(matchSettingsScreens(...))`. These assert the shared pair
  // behaves as both callers assume, which is the whole reason it was extracted: two copies is how
  // the index narrowed to one card while the rail beside it still listed all thirty-eight.
  const t = (key: string) => key;

  test("an empty query matches everything", () => {
    assert.equal(matchSettingsScreens(SETTINGS_SCREENS, "   ", t).length, SETTINGS_SCREENS.length);
  });

  test("every word must match, not any", () => {
    const screens: SettingsScreen[] = [
      { key: "a", href: "/a", titleKey: "google workspace", subtitleKey: "", group: "integrations" },
      { key: "b", href: "/b", titleKey: "google ads", subtitleKey: "", group: "integrations" },
    ];
    assert.deepEqual(
      matchSettingsScreens(screens, "google ads", t).map((s) => s.key),
      ["b"],
    );
  });

  test("keywords are searched, so a word off the card still finds the screen", () => {
    const screens: SettingsScreen[] = [
      { key: "m", href: "/m", titleKey: "Mollie", subtitleKey: "", keywordsKey: "ideal", group: "integrations" },
    ];
    assert.equal(matchSettingsScreens(screens, "ideal", t).length, 1);
  });

  test("grouping drops empty groups and empty sections", () => {
    const sections = groupSettingsScreens(
      SETTINGS_SCREENS.filter((screen) => screen.group === "integrations"),
    );
    assert.equal(sections.length, 1, "only the org section should survive");
    assert.deepEqual(
      sections[0].groups.map((group) => group.key),
      ["integrations"],
    );
  });
});
