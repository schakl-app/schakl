#!/usr/bin/env node
// docs:check — the docs counterpart of i18n:check (issue #136).
//
// Two guarantees, mirroring the messages/ rules in CLAUDE.md §8:
//  1. The nl and en docs trees carry exactly the same pages — locale drift fails.
//     (Sveltia's save_all_locales lets an entry exist in one language; the site falls
//     back to Dutch, but this check keeps "translated everything" the enforced norm.)
//  2. Required pages exist in both locales.
//
// Module coverage: every entry in EXPECTED_MODULES should eventually have
// modules/<name>.mdx. Until the backfill (issue #136) lands, missing module docs are
// reported as TODO warnings; run with DOCS_CHECK_STRICT=1 to make them fatal (CI flips
// this on once the backfill is complete).
//
// TODO(#136): derive EXPECTED_MODULES from the API module registry instead of this list.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const NL_ROOT = 'apps/site/src/content/docs/nl/docs';
const EN_ROOT = 'apps/site/src/content/docs/en/docs';

const REQUIRED = ['index.mdx', 'admin/installation.mdx', 'admin/upgrades.mdx'];

// Every module or core capability that a tenant can switch on and therefore has to be able to
// read about. Keep this in step with apps/api/app/modules/ + the user-visible cores; a module
// that ships without its page is a screen somebody meets with no explanation.
const EXPECTED_MODULES = [
  'companies',
  'contacts',
  'import-export',
  'projects',
  'tasks',
  'time',
  'leave',
  'hr',
  'domains',
  'websites',
  'hosting',
  'subscriptions',
  'invoicing',
  'marketing',
  'reporting',
  'portal',
  'automation',
  'interactions',
  'notifications',
  'custom-fields',
  'roles',
  'activity',
  'branding',
  'files',
  'bulk-edit',
  'ai',
];

// The integrations folder is checked the same way: an integration the product ships and the
// marketing site links to must have somewhere for that link to land.
const EXPECTED_INTEGRATIONS = [
  'index',
  'mollie',
  'cloudflare',
  'oxxa',
  'google-workspace',
  'marketing-sources',
  'rest-api',
  'mcp',
];

const EXPECTED_ADMIN = [
  'installation',
  'upgrades',
  'licenses',
  'modules',
  'email',
  'storage',
  'sso',
  'two-factor',
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

const missing = [
  ...EXPECTED_MODULES.map((m) => `modules/${m}.mdx`),
  ...EXPECTED_INTEGRATIONS.map((i) => `integrations/${i}.mdx`),
  ...EXPECTED_ADMIN.map((a) => `admin/${a}.mdx`),
].filter((page) => !nl.has(page));

for (const page of missing) {
  const msg = `expected docs page missing: ${page}`;
  if (strict) fail(msg);
  else console.warn(`⚠ TODO ${msg}`);
}

// A docs link that carries no locale is a 404: the tree is symmetric /nl/docs/… + /en/docs/…
// and only the bare /docs redirects. This shipped once, on the first page a Dutch reader opens,
// so it is checked here at source as well as against the built site (scripts/site-links-check.mjs).
const unlocalised = [];
for (const [root, prefix] of [
  [NL_ROOT, '/nl/docs/'],
  [EN_ROOT, '/en/docs/'],
]) {
  for (const page of walk(root)) {
    const body = readFileSync(join(root, page), 'utf8');
    for (const m of body.matchAll(/(?:href=["']|\]\()(\/docs\/[^"')\s]*)/g)) {
      unlocalised.push(`${prefix.slice(1)}${page} → ${m[1]} (should start ${prefix})`);
    }
  }
}
for (const u of unlocalised) fail(`docs link without a locale: ${u}`);

// MDX evaluates `{...}` in prose as JavaScript. That is a problem here specifically because the
// product documents template variables — `{brand}`, `{name}`, `{link}` — and writing one in a
// sentence turns it into an undefined identifier that fails at RENDER time, not at parse time:
// the build gets most of the way through and then dies with "brand is not defined" and a stack
// trace into a hashed chunk, which says nothing about which page is at fault. Backtick it.
const braceHazards = [];
for (const [root, label] of [
  [NL_ROOT, 'nl'],
  [EN_ROOT, 'en'],
]) {
  for (const page of walk(root)) {
    const body = readFileSync(join(root, page), 'utf8');
    let inFence = false;
    body.split('\n').forEach((line, i) => {
      if (/^\s*```/.test(line)) {
        inFence = !inFence;
        return;
      }
      if (inFence) return;
      if (/^\s*import\s/.test(line)) return; // `import { Aside } from …` is the whole point of braces
      // Strip inline code first: `{brand}` is exactly how these should be written.
      const bare = line.replace(/`[^`]*`/g, '');
      // A JSX prop (`order={3}`) or an import line is legitimate; a lone {word} in prose is not.
      for (const m of bare.matchAll(/(^|[^=\w])\{\s*([A-Za-z_$][\w$]*)\s*\}/g)) {
        braceHazards.push(`${label}/${page}:${i + 1} → {${m[2]}} (wrap it in backticks)`);
      }
    });
  }
}
for (const h of braceHazards) fail(`MDX will evaluate this as JavaScript: ${h}`);

// Starlight's autogenerated sidebar sorts by `sidebar.order` and falls back to the filename on a
// tie. A tie is therefore not a harmless duplicate: it silently replaces the order somebody chose
// with alphabetical order, in one group, and nothing anywhere says so.
for (const [root, label] of [
  [NL_ROOT, 'nl'],
  [EN_ROOT, 'en'],
]) {
  const byDir = new Map();
  for (const page of walk(root)) {
    const dir = page.includes('/') ? page.slice(0, page.lastIndexOf('/')) : '.';
    const m = readFileSync(join(root, page), 'utf8').match(/^sidebar:\s*$[\s\S]*?^\s+order:\s*(\d+)/m);
    if (!m) continue;
    const key = `${dir}#${m[1]}`;
    if (byDir.has(key)) {
      fail(
        `two ${label} pages share sidebar.order ${m[1]} in ${dir}/: ` +
          `${byDir.get(key)} and ${page} — the sidebar falls back to filename order`,
      );
    }
    byDir.set(key, page);
  }
}

if (failed) {
  console.error('\ndocs:check failed.');
  process.exit(1);
}
console.log(
  `docs:check ok — ${nl.size} pages in both locales` +
    (missing.length ? `, ${missing.length} expected pages still to write (#136)` : ''),
);
