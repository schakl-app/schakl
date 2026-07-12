#!/usr/bin/env node
// docs:check — the docs counterpart of i18n:check (issue #136).
//
// Two guarantees, mirroring the messages/ rules in CLAUDE.md §8:
//  1. The nl (root) and en docs trees carry exactly the same pages — locale drift fails.
//  2. Required pages exist in both locales.
//
// Module coverage: every entry in EXPECTED_MODULES should eventually have
// modules/<name>.mdx. Until the backfill (issue #136) lands, missing module docs are
// reported as TODO warnings; run with DOCS_CHECK_STRICT=1 to make them fatal (CI flips
// this on once the backfill is complete).
//
// TODO(#136): derive EXPECTED_MODULES from the API module registry instead of this list.

import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const NL_ROOT = 'apps/site/src/content/docs/docs';
const EN_ROOT = 'apps/site/src/content/docs/en/docs';

const REQUIRED = ['index.mdx', 'admin/installation.mdx', 'admin/upgrades.mdx'];

const EXPECTED_MODULES = [
  'companies',
  'contacts',
  'projects',
  'tasks',
  'time',
  'leave',
  'domains',
  'websites',
  'hosting',
  'subscriptions',
  'notifications',
  'custom-fields',
  'roles',
  'branding',
  'files',
];

function walk(dir, base = dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out = out.concat(walk(p, base));
    else if (/\.(md|mdx)$/.test(name)) out.push(relative(base, p));
  }
  return out;
}

const strict = process.env.DOCS_CHECK_STRICT === '1';
let failed = false;
const fail = (msg) => {
  console.error(`✗ ${msg}`);
  failed = true;
};

const nl = new Set(walk(NL_ROOT));
const en = new Set(walk(EN_ROOT));

for (const page of nl) if (!en.has(page)) fail(`missing in en: ${page}`);
for (const page of en) if (!nl.has(page)) fail(`missing in nl (root): ${page}`);

for (const page of REQUIRED) {
  if (!nl.has(page)) fail(`required page missing: ${page}`);
}

const missingModules = EXPECTED_MODULES.filter((m) => !nl.has(`modules/${m}.mdx`));
for (const m of missingModules) {
  const msg = `module without docs page: modules/${m}.mdx`;
  if (strict) fail(msg);
  else console.warn(`⚠ TODO ${msg}`);
}

if (failed) {
  console.error('\ndocs:check failed.');
  process.exit(1);
}
console.log(
  `docs:check ok — ${nl.size} pages in both locales` +
    (missingModules.length ? `, ${missingModules.length} module docs still to write (#136)` : ''),
);
