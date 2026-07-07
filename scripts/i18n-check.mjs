#!/usr/bin/env node
// Fails (non-zero exit) if locale catalogs drift from the source locale.
//
// Rules (see CLAUDE.md §8):
//   - en.json is the SOURCE of truth for keys.
//   - nl.json is REQUIRED and must be complete (never partial) — it is the default UI language.
//   - Every other locale mirrors en's keys exactly (missing OR extra keys both fail).
//
// Message catalogs are flat, namespaced JSON (`companies.title`, `common.save`, …).
// Keys beginning with `$` (e.g. inlang's `$schema`) are metadata and ignored.

import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MESSAGES_DIR = join(ROOT, "messages");

const SOURCE_LOCALE = "en";
const REQUIRED_LOCALES = ["nl"]; // must exist and be complete

/** @param {string} file */
function loadKeys(file) {
  const raw = JSON.parse(readFileSync(join(MESSAGES_DIR, file), "utf8"));
  return new Set(Object.keys(raw).filter((k) => !k.startsWith("$")));
}

const files = readdirSync(MESSAGES_DIR).filter((f) => f.endsWith(".json"));
const locales = files.map((f) => f.replace(/\.json$/, ""));

if (!locales.includes(SOURCE_LOCALE)) {
  console.error(`✖ i18n:check — source locale "${SOURCE_LOCALE}.json" is missing.`);
  process.exit(1);
}
for (const req of REQUIRED_LOCALES) {
  if (!locales.includes(req)) {
    console.error(`✖ i18n:check — required locale "${req}.json" is missing.`);
    process.exit(1);
  }
}

const sourceKeys = loadKeys(`${SOURCE_LOCALE}.json`);
let failed = false;

for (const locale of locales) {
  if (locale === SOURCE_LOCALE) continue;
  const keys = loadKeys(`${locale}.json`);

  const missing = [...sourceKeys].filter((k) => !keys.has(k)).sort();
  const extra = [...keys].filter((k) => !sourceKeys.has(k)).sort();

  if (missing.length || extra.length) {
    failed = true;
    console.error(`\n✖ ${locale}.json is out of sync with ${SOURCE_LOCALE}.json`);
    if (missing.length) console.error(`  missing (${missing.length}): ${missing.join(", ")}`);
    if (extra.length) console.error(`  extra   (${extra.length}): ${extra.join(", ")}`);
  } else {
    console.log(`✓ ${locale}.json — ${keys.size} keys, in sync`);
  }
}

if (failed) {
  console.error(
    `\ni18n:check FAILED. Every locale must mirror ${SOURCE_LOCALE}.json exactly; ` +
      `add/remove keys in all catalogs in the same change.`,
  );
  process.exit(1);
}

console.log(`\n✓ i18n:check passed — ${sourceKeys.size} keys across ${locales.length} locales.`);
