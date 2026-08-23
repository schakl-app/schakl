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
function load(file) {
  const raw = JSON.parse(readFileSync(join(MESSAGES_DIR, file), "utf8"));
  return Object.entries(raw).filter(([key]) => !key.startsWith("$"));
}

/** @param {string} file */
function loadKeys(file) {
  return new Set(load(file).map(([key]) => key));
}

// ICU **plural/select** is not supported by our Paraglide setup: it reads `{…}` as a plain
// placeholder name, so `{count, plural, one {…} other {…}}` compiles to a parameter literally
// called `count, plural, one {{count` and renders as `undefined … } other …}}` in front of the
// user. It compiles without complaint, which is exactly why it is checked here.
//
// The house form is a **pair of keys** — `<key>` and `<key>_one` — read by `tn()` on the web and
// picked by the caller on the API. Not a bracketed suffix: `{count} abonnement(en)` reads as
// machine output, and this file used to recommend it (#343).
const PLURAL_SYNTAX = /\{[^{}]*,\s*(plural|select)\s*,/;

// A counted message that dodges the pair with a parenthesis — "{count} bericht(en)",
// "{count} day(s)". Only ever flagged on a message that *counts* something, so `HTTP(s)` (a
// protocol, not a plural) is not a false positive.
const PARENTHETICAL_PLURAL = /[a-z]\((?:s|n|en|es|'s|’s)\)/i;

/** @param {string} file */
function pluralMessages(file) {
  return load(file)
    .filter(([, value]) => typeof value === "string" && PLURAL_SYNTAX.test(value))
    .map(([key]) => key)
    .sort();
}

/** Counted messages that spell the plural with a parenthesis instead of a `_one` sibling. */
function parentheticalPlurals(file) {
  return load(file)
    .filter(
      ([, value]) =>
        typeof value === "string" &&
        value.includes("{count}") &&
        PARENTHETICAL_PLURAL.test(value),
    )
    .map(([key]) => key)
    .sort();
}

/**
 * `<key>_one` with neither `<key>` nor `<key>_other` beside it.
 *
 * `tn()` reads the plural key and swaps in `_one` at exactly one, so a singular whose plural was
 * renamed or removed is a key nothing can ever reach — and the symptom is the *plural* rendering
 * as its own raw key on screen, which points at the wrong half of the pair.
 *
 * Two spellings of the plural are accepted because both are in the tree: the bare key (`tn()`'s
 * form, and what a counted noun that reads fine unsuffixed uses) and an explicit `_other`, which
 * the recurrence vocabulary uses so neither half of "elke dag" / "elke {count} dagen" is the
 * odd one out. Which one a pair uses is a copy decision; *having* both halves is not.
 */
function orphanSingulars(file) {
  const keys = new Set(load(file).map(([key]) => key));
  return [...keys]
    .filter((key) => {
      if (!key.endsWith("_one")) return false;
      const base = key.slice(0, -"_one".length);
      return !keys.has(base) && !keys.has(`${base}_other`);
    })
    .sort();
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

for (const locale of locales) {
  const offenders = pluralMessages(`${locale}.json`);
  if (offenders.length) {
    failed = true;
    console.error(
      `\n✖ ${locale}.json uses ICU plural/select, which Paraglide does not compile here — ` +
        `it renders "undefined … } other …}}": ${offenders.join(", ")}`,
    );
    console.error(`  Write a "<key>" / "<key>_one" pair and read it with tn().`);
  }

  const bracketed = parentheticalPlurals(`${locale}.json`);
  if (bracketed.length) {
    failed = true;
    console.error(
      `\n✖ ${locale}.json spells a plural with a parenthesis: ${bracketed.join(", ")}`,
    );
    console.error(`  "{count} abonnement(en)" is machine output. Add a "_one" sibling instead.`);
  }

  const orphans = orphanSingulars(`${locale}.json`);
  if (orphans.length) {
    failed = true;
    console.error(`\n✖ ${locale}.json has a "_one" with no plural beside it: ${orphans.join(", ")}`);
    console.error(`  A singular is one half of a pair; tn() reads the plural key.`);
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
