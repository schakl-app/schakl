/**
 * The marketing connect control's vocabulary, and the hosts that read it (#399, #411).
 *
 * Every rule here is one that fails **silently in a browser**, which is why it is asserted
 * against the source text rather than against a rendering. The bug this file exists for was a
 * literal: `MarketingConnectDialog` mounted the account picker with `hasWebsites={false}`
 * written in, so on every screen but the client panel the Rank Math row said *"deze klant heeft
 * nog geen website"* for a client with two — and it read as correct in review, because the value
 * is a boolean in the right place.
 *
 * The issue asks for exactly this: "a sixth source landing in one host and not the other is a
 * compile error or a test failure". The compile-error half is `Record<MarketingSource, …>` in
 * `types.ts`; this is the other half.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import {
  ALL_CONNECTIONS,
  ALL_METRICS,
  ALL_SOURCES,
  DRILLDOWNS,
  HEADLINE_METRICS,
  ORG_KEY_SOURCES,
  SITE_KEY_SOURCES,
} from "../../src/lib/modules/marketing/types.ts";

const read = (path: string) => readFileSync(new URL(`../../src/${path}`, import.meta.url), "utf8");

const PICKERS = "lib/modules/marketing/MarketingSourcePickers.svelte";
const HOSTS = [
  "lib/modules/marketing/MarketingCompanyPanel.svelte",
  "lib/modules/marketing/MarketingConnectDialog.svelte",
];

describe("the source vocabulary is one list", () => {
  test("every credential-kind list is a subset of the sources that are offered", () => {
    // The two constants exist so a source's *credential* is stated once. A source in one of them
    // and not in `ALL_SOURCES` is a source no picker offers, which is how `rankmath` would have
    // become unreachable a second time.
    for (const source of [...ORG_KEY_SOURCES, ...SITE_KEY_SOURCES]) {
      assert.ok(ALL_SOURCES.includes(source), `${source} is not offered by any picker`);
    }
  });

  test("every offered source has a headline row, a metric list and a drill-down list", () => {
    // Typed `Record<MarketingSource, …>`, so a *missing* key is a compile error — this catches
    // the other direction, a key added to `ALL_SOURCES` and nowhere else.
    for (const source of ALL_SOURCES) {
      assert.ok(HEADLINE_METRICS[source]?.length, `${source} has no headline metrics`);
      assert.ok(ALL_METRICS[source]?.length, `${source} has no metric list`);
      assert.ok(DRILLDOWNS[source], `${source} has no drill-down list`);
    }
  });

  test("a connection is never also a source", () => {
    // The whole point of the second list (#411): Tag Manager draws no numbers, so a row of them
    // would have to be invented for it. If a kind ever appears in both, one of the two lists is
    // wrong and a dashboard section that never fills in is what the user sees.
    for (const kind of ALL_CONNECTIONS) {
      assert.ok(
        !(ALL_SOURCES as string[]).includes(kind),
        `${kind} is registered both as a connection and as a metrics source`,
      );
    }
  });
});

describe("both hosts mount the one picker", () => {
  test("no host mounts MarketingAccountPicker itself", () => {
    // Two copies of "which website does this attach to" is how one of them stopped asking.
    for (const host of HOSTS) {
      assert.ok(
        !read(host).includes("<MarketingAccountPicker"),
        `${host} mounts the account picker directly instead of MarketingSourcePickers`,
      );
      assert.ok(read(host).includes("<MarketingSourcePickers"), `${host} mounts no picker at all`);
    }
  });

  test("hasWebsites is derived, never a literal", () => {
    const source = read(PICKERS);
    assert.ok(
      !/hasWebsites=\{(true|false)\}/.test(source),
      "hasWebsites is hardcoded — the exact shape of the bug in #399",
    );
    assert.ok(
      source.includes("const hasWebsites = $derived(websites.length > 0)"),
      "hasWebsites is no longer derived from the client's own websites",
    );
  });

  test("the site question is asked from the offered sources, not from the full list", () => {
    // A host narrowed to Ads (`/marketing/google-ads`) must not grow a website select it cannot
    // use, and a host offering Rank Math must not skip one.
    assert.ok(
      read(PICKERS).includes("sources.some((s) => SITE_KEY_SOURCES.includes(s))"),
      "the picker no longer derives 'is a site part of the answer' from SITE_KEY_SOURCES",
    );
  });
});
