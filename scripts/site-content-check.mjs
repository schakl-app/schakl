#!/usr/bin/env node
// site:content — the marketing site's data files say what the code can actually render.
//
// These JSON files are edited in a CMS by people who cannot run the build, and every one of the
// links between them is a string: a card names an icon by name, a demo by key, a category by key,
// a docs page by path. None of those is checked by `astro build`, which happily renders a card
// with a blank icon, a "Live demo" badge over no demo, and a "Read more" link into a 404.
//
// So: one pass over the data, one failure per broken link, run in CI beside i18n:check.
//
//   node scripts/site-content-check.mjs

import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const SITE = 'apps/site/src';
const FEATURES = `${SITE}/data/features`;
const INTEGRATIONS = `${SITE}/data/integrations`;

let failed = false;
const fail = (msg) => {
  console.error(`✗ ${msg}`);
  failed = true;
};

const read = (dir) =>
  readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => {
      try {
        return { file: f, data: JSON.parse(readFileSync(join(dir, f), 'utf8')) };
      } catch (e) {
        fail(`${dir}/${f} is not valid JSON: ${e.message}`);
        return null;
      }
    })
    .filter(Boolean);

// --- what the code offers -------------------------------------------------------------------
const iconSrc = readFileSync(`${SITE}/components/showcase/Icon.astro`, 'utf8');
const ICONS = new Set([...iconSrc.matchAll(/^\s{2}([A-Za-z][\w]*):/gm)].map((m) => m[1]));

const featurePageSrc = readFileSync(`${SITE}/components/FeaturePage.astro`, 'utf8');
const DEMOS = new Set(
  [...featurePageSrc.matchAll(/^\s{2}([A-Za-z][\w]*):\s*Demo/gm)].map((m) => m[1]),
);

const featuresLib = readFileSync(`${SITE}/lib/features.ts`, 'utf8');
const GROUPED = [...featuresLib.matchAll(/slugs:\s*\[([\s\S]*?)\]/g)].flatMap((m) =>
  [...m[1].matchAll(/'([a-z0-9-]+)'/g)].map((s) => s[1]),
);

const ixLib = readFileSync(`${SITE}/lib/integrations.ts`, 'utf8');
const CATEGORIES = new Set(
  [...ixLib.matchAll(/^\s{4}key:\s*'([a-z0-9-]+)'/gm)].map((m) => m[1]),
);

// The Dutch product name is written with its dot when it is displayed (CLAUDE.md §1), and Dutch
// prose does not take the English em dash as a sentence break (both rules were broken repeatedly).
const nlStrings = (v, out = []) => {
  if (typeof v === 'string') return out;
  if (Array.isArray(v)) {
    v.forEach((x) => nlStrings(x, out));
    return out;
  }
  if (v && typeof v === 'object') {
    for (const [k, x] of Object.entries(v)) {
      if (k === 'nl' && typeof x === 'string') out.push(x);
      else if (k === 'nl' && Array.isArray(x)) out.push(...x.filter((s) => typeof s === 'string'));
      else nlStrings(x, out);
    }
  }
  return out;
};

// --- feature cards --------------------------------------------------------------------------
const features = read(FEATURES);
const seenOrder = new Map();

for (const { file, data: f } of features) {
  const at = `features/${file}`;
  if (f.slug !== file.replace(/\.json$/, '')) fail(`${at}: slug "${f.slug}" ≠ filename`);
  if (!ICONS.has(f.lucide)) fail(`${at}: lucide "${f.lucide}" is not defined in Icon.astro`);
  if (f.demo && !DEMOS.has(f.demo)) {
    fail(
      `${at}: demo "${f.demo}" is in no map in FeaturePage.astro — the card would show a ` +
        `"Live demo" badge over nothing`,
    );
  }
  if (!GROUPED.includes(f.slug)) fail(`${at}: slug is in no group in lib/features.ts`);
  if (seenOrder.has(f.order)) fail(`${at}: order ${f.order} collides with ${seenOrder.get(f.order)}`);
  seenOrder.set(f.order, at);
  for (const loc of ['nl', 'en']) {
    if (!f.title?.[loc]) fail(`${at}: title.${loc} missing`);
    if (!f.description?.[loc]) fail(`${at}: description.${loc} missing`);
  }
  for (const s of nlStrings(f)) {
    if (s.includes('—')) fail(`${at}: em dash in Dutch copy — "${s.slice(0, 70)}…"`);
  }
}
for (const slug of GROUPED) {
  if (!features.some(({ data }) => data.slug === slug)) {
    fail(`lib/features.ts groups "${slug}" but features/${slug}.json does not exist`);
  }
}

// --- integration cards ----------------------------------------------------------------------
const integrations = read(INTEGRATIONS);
const ixOrder = new Map();

for (const { file, data: i } of integrations) {
  const at = `integrations/${file}`;
  if (i.slug !== file.replace(/\.json$/, '')) fail(`${at}: slug "${i.slug}" ≠ filename`);
  if (!ICONS.has(i.lucide)) fail(`${at}: lucide "${i.lucide}" is not defined in Icon.astro`);
  if (!CATEGORIES.has(i.category)) {
    fail(`${at}: category "${i.category}" is in no category in lib/integrations.ts — it renders nowhere`);
  }
  if (!['live', 'beta', 'planned'].includes(i.status)) fail(`${at}: unknown status "${i.status}"`);
  if (ixOrder.has(i.order)) fail(`${at}: order ${i.order} collides with ${ixOrder.get(i.order)}`);
  ixOrder.set(i.order, at);

  // The status vocabulary is the whole point of the page, so its two invariants are enforced:
  // a roadmap card must explain itself and must not link to a guide that cannot exist.
  if (i.status === 'planned') {
    if (i.docs) fail(`${at}: status "planned" but it links to ${i.docs} — there is nothing to document`);
    if (!i.roadmapNote?.nl || !i.roadmapNote?.en) fail(`${at}: status "planned" without a roadmapNote`);
    if (i.page?.steps) fail(`${at}: status "planned" but it carries setup steps`);
  } else {
    if (!i.docs) fail(`${at}: no docs link — the card's "Read more" would go nowhere`);
    if (i.roadmapNote) fail(`${at}: roadmapNote on a ${i.status} integration`);
  }
  for (const s of nlStrings(i)) {
    if (s.includes('—')) fail(`${at}: em dash in Dutch copy — "${s.slice(0, 70)}…"`);
  }
}

// --- landing blocks -------------------------------------------------------------------------
const landing = JSON.parse(readFileSync(`${SITE}/data/landing.json`, 'utf8'));
const showcaseSrc = readFileSync(`${SITE}/components/showcase/Showcase.astro`, 'utf8');
const TOUR_DEMOS = new Set(
  [...showcaseSrc.matchAll(/^\s{2}([A-Za-z][\w]*):\s*Demo/gm)].map((m) => m[1]),
);

for (const [loc, page] of Object.entries(landing)) {
  for (const block of page.blocks ?? []) {
    if (block.type === 'showcase') {
      for (const item of block.items ?? []) {
        // Showcase.astro drops an unknown demo rather than crashing, so the symptom of a typo is
        // a tour row that silently disappears — which is exactly why it is checked here.
        if (!TOUR_DEMOS.has(item.demo)) {
          fail(`landing.json ${loc}: showcase item "${item.demo}" has no component; the row is dropped`);
        }
      }
    }
    if (block.type === 'integrations') {
      for (const slug of block.slugs ?? []) {
        if (!integrations.some(({ data }) => data.slug === slug)) {
          fail(`landing.json ${loc}: integrations strip names "${slug}", which has no card`);
        }
      }
    }
  }
}

// --- CMS model ------------------------------------------------------------------------------
// A collection the CMS cannot see is a file only a developer can edit, which defeats the point
// of "everything is content".
const cms = readFileSync('apps/site/public/sveltia/config.yml', 'utf8');
for (const [name, dir] of [
  ['features', FEATURES],
  ['integrations', INTEGRATIONS],
]) {
  if (!cms.includes(`folder: ${dir}`)) {
    fail(`public/sveltia/config.yml has no collection for ${dir} — it is not editable in the CMS`);
  }
  if (!existsSync(dir)) fail(`${dir} does not exist`);
}

if (failed) {
  console.error('\nsite:content failed.');
  process.exit(1);
}
console.log(
  `site:content ok — ${features.length} feature cards, ${integrations.length} integrations, ` +
    `${ICONS.size} icons, ${DEMOS.size} demos, all references resolve.`,
);
