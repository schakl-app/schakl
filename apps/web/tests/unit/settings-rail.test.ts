/**
 * Every Instellingen screen carries the section rail — including the ones that are not under
 * `/settings/`.
 *
 * Three settings screens are administered on their own module's working page (#229): Taaksjablonen
 * on `/tasks/templates`, Standaardabonnementen on `/subscriptions/templates`, Domeinprijzen on
 * `/domains/tld-prices`. The rail lists them like any other screen, so clicking one used to drop
 * the visitor out of the section and take the menu with it — the rail exists to say what else lives
 * in Instellingen, and it went quiet on exactly the screens hardest to find your way back from.
 *
 * `settings/+layout.svelte` cannot fix that: a route layout only wraps its own subtree. Each such
 * screen mounts `SettingsShell` itself. This test is the thing that notices when the next one is
 * added — a registry entry pointing outside `/settings/` with no shell renders no rail, and nothing
 * else in the build would say so.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { SETTINGS_SCREENS } from "../../src/lib/core/settings-nav.ts";

const ROUTES = join(dirname(fileURLToPath(import.meta.url)), "../../src/routes/(app)");

/** The screens the settings route layout cannot reach. */
const outsideSettings = SETTINGS_SCREENS.filter((s) => !s.href.startsWith("/settings/"));

describe("the Instellingen rail reaches every screen it lists", () => {
  test("the registry still holds screens outside /settings/", () => {
    // If this ever goes to zero the rule below is vacuous, and the reader should be told why
    // rather than left with a green test that asserts nothing.
    assert.ok(
      outsideSettings.length > 0,
      "no settings screen lives outside /settings/ any more — this file can go",
    );
  });

  for (const screen of outsideSettings) {
    test(`${screen.key} (${screen.href}) mounts SettingsShell`, () => {
      const layout = join(ROUTES, screen.href.replace(/^\//, ""), "+layout.svelte");
      let source: string;
      try {
        source = readFileSync(layout, "utf8");
      } catch {
        assert.fail(
          `${screen.href} is listed in the Instellingen rail but has no ${layout} — ` +
            "opening it from Instellingen would lose the section menu.",
        );
      }
      assert.match(
        source,
        /<SettingsShell\b/,
        `${layout} exists but does not render <SettingsShell>.`,
      );
    });
  }
});
