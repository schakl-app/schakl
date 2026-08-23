/**
 * Every settings card's description is one sentence, and it ends like one (#355).
 *
 * The Instellingen index draws 47 cards, each a title plus a one-line description, and 10 of them
 * had lost their full stop. Nothing could notice: each subtitle is a separate key in a 5,000-key
 * catalogue, written months apart by whoever added the screen, and the drift is only visible when
 * the whole grid is on screen at once — which is exactly where a reader's eye catches it going
 * down the page.
 *
 * The rule is punctuation, so the check is punctuation: a card description is a full sentence or a
 * full noun phrase, and it is terminated. Titles are the other half — they are labels, so they
 * carry no stop at all, and asserting both is what stops the fix from swinging the other way.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { SETTINGS_SCREENS } from "../../src/lib/core/settings-nav.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../../..");

function catalogue(locale: string): Record<string, string> {
  return JSON.parse(readFileSync(join(ROOT, "messages", `${locale}.json`), "utf8"));
}

const LOCALES = ["en", "nl"] as const;

describe("settings card copy", () => {
  for (const locale of LOCALES) {
    const messages = catalogue(locale);

    test(`${locale}: every card description ends in a full stop`, () => {
      const missing = SETTINGS_SCREENS.filter((screen) => {
        const text = (messages[screen.subtitleKey] ?? "").trim();
        return text !== "" && !/[.!?]$/.test(text);
      }).map((screen) => screen.subtitleKey);
      assert.deepEqual(missing, [], `no closing stop: ${missing.join(", ")}`);
    });

    test(`${locale}: every card description is translated`, () => {
      const absent = SETTINGS_SCREENS.filter(
        (screen) => !(messages[screen.subtitleKey] ?? "").trim(),
      ).map((screen) => screen.subtitleKey);
      assert.deepEqual(absent, []);
    });

    test(`${locale}: a card title is a label, so it carries no stop`, () => {
      const stopped = SETTINGS_SCREENS.filter((screen) =>
        /[.!?]$/.test((messages[screen.titleKey] ?? "").trim()),
      ).map((screen) => screen.titleKey);
      assert.deepEqual(stopped, []);
    });
  }
});
