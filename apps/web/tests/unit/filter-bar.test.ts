/**
 * Every list screen is filtered by the one shared bar (#354, docs/UX.md).
 *
 * The bar existed and four screens used it; the other seven each kept a hand-written copy of the
 * same twelve lines. That is not a style disagreement, it is how the copies drift: chips ended up
 * styled four different ways, each toolbar ordered itself differently, `/subscriptions` never grew
 * the `?q=` box every comparable list has, and its "wissen" deleted three hand-named keys so a
 * fourth filter would have survived being cleared.
 *
 * A rule stated in a doc is a rule the next screen does not read, so it is a test. Two shapes are
 * refused on any route that mounts a `DataTable`: a `SearchInput` of its own (the bar renders the
 * search box), and a `<button>` that toggles a filter chip by hand.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ROUTES = join(here, "../../src/routes/(app)");

/**
 * Screens that draw a table and are deliberately not filtered lists.
 *
 * Each is a *report* or a queue over a period rather than a register: what narrows it is a date
 * range, a person or a tab, and there is no `?q=` to offer. They are named here rather than
 * detected, because "has no filters" and "forgot the bar" look identical from the outside.
 */
const NOT_A_FILTERED_LIST = new Set([
  "overview/+page.svelte",
  "overview/marketing/+page.svelte",
  "overview/productivity/+page.svelte",
  "overview/revenue/+page.svelte",
  "leave/+page.svelte",
  "leave/team/+page.svelte",
  "leave/availability/+page.svelte",
  "notifications/+page.svelte",
  "invoices/uninvoiced/+page.svelte",
  "domains/tld-prices/+page.svelte",
  "time/+page.svelte",
]);

function walk(dir: string): string[] {
  let out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out = out.concat(walk(path));
    else if (entry === "+page.svelte") out.push(path);
  }
  return out;
}

/** Every route that mounts a `DataTable` — the screens this rule is about. */
function listRoutes(): { rel: string; src: string }[] {
  return walk(ROUTES)
    .map((path) => ({ rel: relative(ROUTES, path), src: readFileSync(path, "utf8") }))
    .filter(({ src }) => src.includes("<DataTable"));
}

describe("the shared filter bar", () => {
  const routes = listRoutes();

  test("there are list routes to check", () => {
    assert.ok(routes.length >= 10, `only ${routes.length} list routes found`);
  });

  test("every filtered list renders FilterBar", () => {
    const missing = routes
      .filter(({ rel }) => !NOT_A_FILTERED_LIST.has(rel))
      .filter(({ src }) => !src.includes("<FilterBar"))
      .map(({ rel }) => rel);
    assert.deepEqual(missing, [], `no <FilterBar>: ${missing.join(", ")}`);
  });

  test("no list route mounts its own search box", () => {
    const own = routes.filter(({ src }) => src.includes("<SearchInput")).map(({ rel }) => rel);
    assert.deepEqual(own, [], `hand-mounted SearchInput: ${own.join(", ")}`);
  });

  test("no list route hand-rolls a filter chip", () => {
    // The tell is a `<button>` whose click handler writes a filter parameter: the shape every
    // one of these screens had before the bar, and the one that drifts.
    const chip = /onclick=\{\(\)\s*=>\s*set(?:Filter|StatusFilter|TypeFilter|AssigneeFilter)\(/;
    const offenders = routes
      .filter(({ rel }) => !NOT_A_FILTERED_LIST.has(rel))
      .filter(({ src }) => {
        for (const match of src.matchAll(new RegExp(chip, "g"))) {
          // A summary tile is a legitimate outside-the-bar control (docs/UX.md, UX §7): it is a
          // card, not a chip. The chip shape is the one inside a rounded-full button.
          const before = src.slice(Math.max(0, match.index - 400), match.index);
          if (before.includes("rounded-full")) return true;
        }
        return false;
      })
      .map(({ rel }) => rel);
    assert.deepEqual(offenders, [], `hand-rolled filter chips: ${offenders.join(", ")}`);
  });
});
