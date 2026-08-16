/**
 * Last-known tenant branding (`$lib/core/tenant-cache.server.ts`).
 *
 * It has exactly one reader — the outage page — so its whole job is to still hold the tenant's
 * colours at the moment the API cannot be asked for them. Two properties make or break that, and
 * neither shows up in a screenshot: an unresolved host must never be remembered (it has no
 * branding, and caching `DEFAULT_THEME` under a real hostname would pin a tenant to the neutral
 * palette until the process restarted), and the map must stay bounded on a cloud process that
 * anyone can send arbitrary `Host` headers to.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import { DEFAULT_THEME } from "../../src/lib/core/theme.ts";
import {
  clearThemeCache,
  lastKnownTheme,
  rememberTheme,
} from "../../src/lib/core/tenant-cache.server.ts";

const resolved = (brandName: string) => ({ ...DEFAULT_THEME, brandName, resolved: true });

describe("tenant theme cache", () => {
  beforeEach(() => clearThemeCache());

  test("hands back the branding this host last resolved to", () => {
    rememberTheme("acme.example", resolved("Acme"));
    assert.equal(lastKnownTheme("acme.example")?.brandName, "Acme");
  });

  test("keyed by host, because one process serves many tenants", () => {
    rememberTheme("a.example", resolved("A"));
    rememberTheme("b.example", resolved("B"));
    assert.equal(lastKnownTheme("a.example")?.brandName, "A");
    assert.equal(lastKnownTheme("b.example")?.brandName, "B");
  });

  test("a host this process has never served has no branding, and says so", () => {
    // `null`, not `DEFAULT_THEME` — the caller decides what neutral looks like, and a fresh
    // replica genuinely knows nothing until it has answered one good request.
    assert.equal(lastKnownTheme("unseen.example"), null);
    assert.equal(lastKnownTheme(null), null);
  });

  test("an unresolved host is not remembered", () => {
    rememberTheme("nobody.example", { ...DEFAULT_THEME, resolved: false });
    assert.equal(lastKnownTheme("nobody.example"), null);
  });

  test("bounded, dropping the least recently seen host first", () => {
    for (let i = 0; i < 64; i++) rememberTheme(`h${i}.example`, resolved(`H${i}`));
    // Touch the oldest so it is no longer the oldest, then overflow by one.
    rememberTheme("h0.example", resolved("H0"));
    rememberTheme("new.example", resolved("New"));

    assert.equal(lastKnownTheme("new.example")?.brandName, "New");
    assert.equal(lastKnownTheme("h0.example")?.brandName, "H0", "re-seen host must survive");
    assert.equal(lastKnownTheme("h1.example"), null, "the quietest host is the one evicted");
  });
});
