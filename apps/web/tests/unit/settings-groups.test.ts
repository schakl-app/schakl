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
  settingsScreenForModule,
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
      assert.ok(
        known.has(screen.group),
        `screen '${screen.key}' names unknown group '${screen.group}'`,
      );
    }
  });
});

describe("the switch heads the group it switches (issue #378)", () => {
  // "Modules" used to name two things on one page: a card in Werkruimte that enabled things, and
  // a group heading fourteen cards down that collected their settings. Putting each enablement
  // screen *first in the group it governs* is what makes the word mean one thing in each place —
  // and it is a position, so only a positional assertion can hold it.
  for (const [group, key] of [
    ["modules", "modules"],
    ["integrations", "integrations"],
  ] as const) {
    test(`${key} is the first screen in the ${group} group`, () => {
      const items = SETTINGS_SCREENS.filter((screen) => screen.group === group);
      assert.ok(items.length > 1, `the ${group} group holds nothing to switch on`);
      assert.equal(
        items[0].key,
        key,
        `'${key}' must lead the ${group} group — a list of settings for things you cannot ` +
          "switch on is the state #378 was raised about.",
      );
    });
  }

  test("neither enablement screen claims a module of its own", () => {
    // They enable *every* module, so declaring one would hide the screen the moment a tenant
    // switched that module off — including, for `modules`, the way back.
    for (const key of ["modules", "integrations"]) {
      const screen = SETTINGS_SCREENS.find((s) => s.key === key);
      assert.ok(screen, `no '${key}' screen`);
      assert.equal(screen!.module, undefined);
    }
  });
});

describe("a row on the enablement screens links onward only when that is unambiguous", () => {
  test("an integration with one settings screen resolves to it", () => {
    assert.equal(settingsScreenForModule("cloudflare")?.href, "/settings/cloudflare");
    assert.equal(settingsScreenForModule("mollie")?.href, "/settings/mollie");
  });

  test("a module owning several settings screens resolves to none", () => {
    // `tasks` owns labels, statuses and templates. Picking one would be picking arbitrarily on
    // the reader's behalf, and a link that lands on one of three is worse than no link.
    assert.ok(SETTINGS_SCREENS.filter((s) => s.module === "tasks").length > 1);
    assert.equal(settingsScreenForModule("tasks"), null);
  });

  test("a module owning no settings screen resolves to none", () => {
    assert.equal(settingsScreenForModule("wordpress"), null);
    assert.equal(settingsScreenForModule("nope-not-a-module"), null);
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
      {
        key: "a",
        href: "/a",
        titleKey: "google workspace",
        subtitleKey: "",
        group: "integrations",
      },
      { key: "b", href: "/b", titleKey: "google ads", subtitleKey: "", group: "integrations" },
    ];
    assert.deepEqual(
      matchSettingsScreens(screens, "google ads", t).map((s) => s.key),
      ["b"],
    );
  });

  test("keywords are searched, so a word off the card still finds the screen", () => {
    const screens: SettingsScreen[] = [
      {
        key: "m",
        href: "/m",
        titleKey: "Mollie",
        subtitleKey: "",
        keywordsKey: "ideal",
        group: "integrations",
      },
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
