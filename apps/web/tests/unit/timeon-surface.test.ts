/**
 * Where the Timeon sync workspace is reachable from (#389).
 *
 * `timeon` was the only integration with an entry in the **main** navigation — position 71,
 * directly under Uren — where `snelstart`, `mollie`, `cloudflare`, `oxxa` and `wordpress` all live
 * under Instellingen → Integraties with their working surfaces attached to the records they are
 * about. The argument for the exception was that a two-way sync produces a queue somebody has to
 * settle, which is a good argument for the queue being *reachable* and not one for a permanent
 * top-level slot: a cutover ends, and until it does the queue is empty most days.
 *
 * Three assertions, and each one is a thing that cannot be seen by reading a diff: a nav item is
 * one object in a list of forty, a settings entry is one object in a list of thirty, and the
 * conditional in front of a banner is one line in a 1200-line screen. Run with
 * `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { SETTINGS_SCREENS } from "../../src/lib/core/settings-nav.ts";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "../../src");
const read = (path: string): string => readFileSync(join(SRC, path), "utf8");

describe("the Timeon workspace is reached from where the work is", () => {
  test("the web module contributes no nav item", () => {
    const source = read("lib/integrations/timeon/index.ts");
    assert.ok(
      !/\bnav\s*:/.test(source),
      "a cutover queue is not a top-level destination: `/timeon` is reached from Instellingen " +
        "→ Integraties and from the Uren strip, never from a permanent menu slot",
    );
  });

  test("Instellingen → Integraties still carries it, and it links through to the workspace", () => {
    const entry = SETTINGS_SCREENS.find((screen) => screen.key === "timeon");
    assert.ok(entry, "removing the nav item must not remove the only remaining way in");
    assert.equal(entry?.group, "integrations");
    assert.equal(entry?.module, "timeon");
    assert.ok(
      entry?.permissions?.includes("timeon.sync.run"),
      "the gate stays `timeon.sync.run` wherever the entry ends up",
    );
    assert.match(
      read("routes/(app)/settings/timeon/+page.svelte"),
      /href="\/timeon\?account=/,
      "the settings screen is the way in, so it has to link through",
    );
  });

  test("Uren points at the queue only when there is something in it", () => {
    const page = read("routes/(app)/time/+page.svelte");
    assert.match(
      page,
      /\{#if data\.timeonConflicts > 0\}/,
      "a strip that is drawn every day showing nothing is the thing people stop reading",
    );
    assert.match(page, /href="\/timeon"/);
    // The count is read in the *layout*, so a month of week clicks does not re-ask for it.
    assert.match(
      read("routes/(app)/time/+layout.server.ts"),
      /timeon\.sync\.run/,
      "the pointer is gated on the permission the workspace itself declares",
    );
  });
});
