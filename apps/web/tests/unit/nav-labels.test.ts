/**
 * A sidebar item is named after the page it opens, and no two of them are named the same (#351).
 *
 * The sidebar had **two** items reading "Overzicht" — one opening `/marketing`, headed *Marketing*,
 * and one opening `/overview`, headed *Urenoverzicht*. Neither label named its page, both
 * breadcrumbs read "Overzicht" as well, and the two rows measured identically, so nothing on the
 * screen could tell a reader which was which. Nothing in the build could either: a label is a
 * lookup of a key that exists, and two keys holding one word is not a type error.
 *
 * So it is compared to something now. The labels are read out of the module registries as source
 * text — importing `$lib/core/registry` would drag Svelte components in under a plain node runner
 * — and the assertion is about the strings a tenant actually reads, in both locales, because a
 * collision can exist in one language and not the other.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const LIB = join(here, "../../src/lib");
const LAYOUT = join(here, "../../src/routes/(app)/+layout.svelte");
const MESSAGES = join(here, "../../../../messages");

const CATALOGUES = {
  en: JSON.parse(readFileSync(join(MESSAGES, "en.json"), "utf8")) as Record<string, string>,
  nl: JSON.parse(readFileSync(join(MESSAGES, "nl.json"), "utf8")) as Record<string, string>,
};

/** `{ key, href, labelKey }` for every nav item a module or integration contributes. */
function registryNavItems(): { key: string; href: string; labelKey: string }[] {
  const found: { key: string; href: string; labelKey: string }[] = [];
  for (const root of ["modules", "integrations"]) {
    for (const dir of readdirSync(join(LIB, root), { withFileTypes: true })) {
      if (!dir.isDirectory()) continue;
      let src: string;
      try {
        src = readFileSync(join(LIB, root, dir.name, "index.ts"), "utf8");
      } catch {
        continue;
      }
      // Each entry states key, href and label within a few lines of each other; reading the trio
      // together is what keeps an item whose label lives elsewhere from being silently skipped.
      const re =
        /key:\s*"([^"]+)",\s*\n\s*href:\s*"([^"]+)",\s*\n\s*label:\s*\(\)\s*=>\s*t\("([^"]+)"\)/g;
      for (const m of src.matchAll(re)) found.push({ key: m[1], href: m[2], labelKey: m[3] });
    }
  }
  return found;
}

/**
 * The items the app layout hardcodes rather than contributing through a module — the dashboard,
 * the org-wide overviews, Instellingen, the instance console. Named here because they are not in
 * any registry, and asserted to still be in the layout so this list cannot quietly go stale.
 */
const LAYOUT_ITEMS = [
  { href: "/", labelKey: "nav.dashboard" },
  { href: "/overview", labelKey: "nav.overview" },
  { href: "/settings", labelKey: "nav.settings" },
  { href: "/instance", labelKey: "nav.instance" },
];

describe("sidebar labels", () => {
  const items = registryNavItems();

  test("the registries contribute nav items at all", () => {
    assert.ok(items.length >= 10, `only found ${items.length} nav items — did the shape change?`);
  });

  test("the hardcoded layout items are still in the layout", () => {
    const src = readFileSync(LAYOUT, "utf8");
    for (const item of LAYOUT_ITEMS) {
      assert.ok(src.includes(`href="${item.href}"`), `${item.href} is no longer in the layout`);
      assert.ok(src.includes(`t("${item.labelKey}")`), `${item.labelKey} is no longer read`);
    }
  });

  for (const [locale, messages] of Object.entries(CATALOGUES)) {
    test(`${locale}: every nav label is translated`, () => {
      const untranslated = [...items, ...LAYOUT_ITEMS]
        .map((item) => item.labelKey)
        .filter((key) => !(messages[key] ?? "").trim());
      assert.deepEqual(untranslated, []);
    });

    test(`${locale}: no two nav items share a label`, () => {
      const byLabel = new Map<string, string[]>();
      for (const item of [...items, ...LAYOUT_ITEMS]) {
        const label = (messages[item.labelKey] ?? item.labelKey).trim();
        byLabel.set(label, [...(byLabel.get(label) ?? []), item.href]);
      }
      const clashes = [...byLabel]
        .filter(([, hrefs]) => new Set(hrefs).size > 1)
        .map(([label, hrefs]) => `${label} → ${[...new Set(hrefs)].join(", ")}`);
      assert.deepEqual(clashes, []);
    });
  }
});
