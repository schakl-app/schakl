#!/usr/bin/env node
// site:feature-order — renumber the feature cards so `order` agrees with the grouping.
//
// Two places sort the same cards and they were disagreeing. `src/lib/features.ts` groups them by
// theme for the mega-menu and the /features/ index; `order` sorts the flat list on the home grid
// and the icon quick-menu. When cards are added one at a time by different hands, `order` drifts
// into collisions (two cards on 60 sort arbitrarily) and into an ordering that has nothing to do
// with the groups, so the same fourteen cards appear in two different sequences on two pages of
// the same site.
//
// This makes the grouping the single source of truth: group order, then position within the group,
// numbered in tens so a card can still be nudged by hand between two others in the CMS.
//
// Run it after adding a card:  node scripts/site-feature-order.mjs
// Check without writing:       node scripts/site-feature-order.mjs --check   (non-zero if stale)

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const DIR = 'apps/site/src/data/features';
const LIB = 'apps/site/src/lib/features.ts';
const check = process.argv.includes('--check');

// The groups live in TypeScript, which this plain script cannot import. Reading the `slugs`
// arrays out of the source keeps one definition rather than a second copy that would rot.
const lib = readFileSync(LIB, 'utf8');
const ordered = [...lib.matchAll(/slugs:\s*\[([\s\S]*?)\]/g)].flatMap((m) =>
  [...m[1].matchAll(/'([a-z0-9-]+)'/g)].map((s) => s[1]),
);

if (!ordered.length) {
  console.error(`✗ found no group slugs in ${LIB} — has its shape changed?`);
  process.exit(1);
}

const files = readdirSync(DIR).filter((f) => f.endsWith('.json'));
const cards = new Map(files.map((f) => [f, JSON.parse(readFileSync(join(DIR, f), 'utf8'))]));

const bySlug = new Map([...cards].map(([f, c]) => [c.slug, { file: f, card: c }]));

const orphans = [...bySlug.keys()].filter((s) => !ordered.includes(s));
const missing = ordered.filter((s) => !bySlug.has(s));
let failed = false;
for (const s of orphans) {
  console.error(`✗ ${s}.json is in no group in ${LIB} — it renders nowhere but the home grid.`);
  failed = true;
}
for (const s of missing) {
  console.error(`✗ ${LIB} lists "${s}" but ${DIR}/${s}.json does not exist.`);
  failed = true;
}
if (failed) process.exit(1);

const stale = [];
ordered.forEach((slug, i) => {
  const want = (i + 1) * 10;
  const { file, card } = bySlug.get(slug);
  if (card.order === want) return;
  stale.push(`${slug}: ${card.order} → ${want}`);
  if (!check) {
    card.order = want;
    writeFileSync(join(DIR, file), JSON.stringify(card, null, 2) + '\n');
  }
});

if (check && stale.length) {
  console.error(`✗ ${stale.length} feature card(s) out of order:\n  ${stale.join('\n  ')}`);
  console.error('\nRun: node scripts/site-feature-order.mjs');
  process.exit(1);
}
console.log(
  stale.length
    ? `site:feature-order — renumbered ${stale.length} card(s):\n  ${stale.join('\n  ')}`
    : `site:feature-order ok — ${ordered.length} cards, order matches the grouping.`,
);
