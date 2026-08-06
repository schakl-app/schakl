#!/usr/bin/env node
// license:check — the commercial boundary, enforced rather than remembered.
//
// `LICENSE-COMMERCIAL.md` defines its own covered set: *"the directories of modules that
// declare a `sku` on their `ModuleDescriptor` (issue #137), and their web counterparts"*.
// That is a rule about the code, written in a markdown file, and checked by whoever happened
// to look — so it drifted three separate ways at once:
//
//   * `reporting` declared `sku="reporting"` from the day it shipped and carried no marker in
//     any of its four directories, and appeared nowhere in the list;
//   * `portal`'s API half was marked and listed while its web half was neither, so half a
//     licensed module sat under the AGPL;
//   * two directories carried a marker the list never mentioned.
//
// None of that is visible in a diff, none of it breaks a test, and all of it is about which
// license a file ships under. So it gets the same treatment as `i18n:check` and `forms:check`.
//
// Four rules, checked in both directions:
//
//   1. every module declaring a `sku` carries a marker on its API directory, and on its web
//      counterpart where one exists — the rule that would have caught `reporting`;
//   2. every directory carrying a marker is listed in `LICENSE-COMMERCIAL.md`;
//   3. every directory the list names exists and carries a marker;
//   4. every marker is byte-identical, so "is this covered" is never a reading exercise.
//
// **What it cannot see**, stated so nobody mistakes a pass for a proof: route directories.
// `invoicing` covers `routes/(app)/invoices/`, `routes/(app)/quotes/` *and*
// `routes/(app)/settings/invoicing/`, which no rule derives from the name `invoicing`. Rules
// 2–4 keep those honest once somebody marks them; only a human decides that a new screen
// belongs to a licensed module. Rule 1 is the mechanical half, and it is the half that failed.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const DOC = 'LICENSE-COMMERCIAL.md';
const API_MODULES = 'apps/api/app/modules';
const WEB_MODULES = 'apps/web/src/lib/modules';

/** Never walked: vendored dependencies and build output carry licenses that are not ours. */
const SKIP = new Set(['node_modules', '.venv', '.svelte-kit', 'dist', 'build', '__pycache__']);

/**
 * A `sku=` that is really a keyword argument, not a mention of one in a docstring.
 *
 * The trailing comma is what tells them apart: `app/modules/google/__init__.py` explains the
 * entitlement in prose and quotes `sku="google"` mid-sentence, which a laxer pattern reads as
 * a second declaration.
 */
const SKU = /^\s*sku="[a-z0-9_]+",/m;

function walk(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (SKIP.has(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name === 'LICENSE') out.push(p);
  }
  return out;
}

function isDir(p) {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

const problems = [];

// --- the three sets ------------------------------------------------------------------- //
const markerFiles = walk('apps').sort();
const marked = new Set(markerFiles.map((f) => `${relative('.', join(f, '..'))}/`));

const doc = readFileSync(DOC, 'utf8');
const listed = new Set(
  [...doc.matchAll(/^- `([^`]+)`/gm)].map((m) => m[1]).filter((p) => p.startsWith('apps/')),
);

const sku = readdirSync(API_MODULES)
  .filter((name) => {
    const init = join(API_MODULES, name, '__init__.py');
    try {
      return SKU.test(readFileSync(init, 'utf8'));
    } catch {
      return false;
    }
  })
  .sort();

// --- 1. a licensed module is marked on both halves ------------------------------------ //
for (const name of sku) {
  const dirs = [`${API_MODULES}/${name}/`];
  // Only where a web counterpart exists: `hr` is API-only, and demanding a marker on a
  // directory nobody has written yet would be a check about the future.
  if (isDir(`${WEB_MODULES}/${name}`)) dirs.push(`${WEB_MODULES}/${name}/`);
  for (const dir of dirs) {
    if (!marked.has(dir)) {
      problems.push(
        `✗ ${dir} — '${name}' declares a sku, so this directory is commercially licensed ` +
          `and needs a LICENSE marker. Copy one: cp ${API_MODULES}/marketing/LICENSE ${dir}`,
      );
    }
  }
}

// --- 2 & 3. the list and the files agree, both ways ----------------------------------- //
for (const dir of [...marked].sort()) {
  if (!listed.has(dir)) {
    problems.push(
      `✗ ${dir} — carries a LICENSE marker but is not listed in ${DOC}. A covered directory ` +
        `the license does not name is covered by nothing; add it under "Covered directories".`,
    );
  }
}
for (const dir of [...listed].sort()) {
  if (!isDir(dir)) {
    problems.push(`✗ ${dir} — listed in ${DOC} but no such directory exists.`);
  } else if (!marked.has(dir)) {
    problems.push(
      `✗ ${dir} — listed in ${DOC} but carries no LICENSE marker, and the license says every ` +
        `covered directory does. Copy one: cp ${API_MODULES}/marketing/LICENSE ${dir}`,
    );
  }
}

// --- 4. one marker text ---------------------------------------------------------------- //
const canonical = readFileSync(join(API_MODULES, 'marketing', 'LICENSE'), 'utf8');
for (const file of markerFiles) {
  if (readFileSync(file, 'utf8') !== canonical) {
    problems.push(
      `✗ ${relative('.', file)} — marker text differs from ${API_MODULES}/marketing/LICENSE. ` +
        `One wording, so "is this covered" is never a reading exercise.`,
    );
  }
}

if (problems.length) {
  for (const line of problems) console.error(line);
  console.error(
    `\nlicense:check failed. See ${DOC} — the covered set is "the directories of modules ` +
      `that declare a sku, and their web counterparts".\n` +
      `Route directories are not derivable from a module name and are yours to add by hand; ` +
      `everything else above is mechanical.`,
  );
  process.exit(1);
}

console.log(
  `license:check ok — ${sku.length} licensed modules, ${marked.size} covered directories, ` +
    `all marked and listed`,
);
