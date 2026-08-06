#!/usr/bin/env node
// site:marks — every <IntegrationMark> must be told which integration it is drawing.
//
// The mark resolves a vendor logo from `slug`: a supplied file in src/assets/brands/, else the
// generated Simple Icons set, else the monogram. Leaving `slug` off is not an error anywhere —
// it silently falls back to the monogram, which is a legitimate rendering for a vendor that has
// no logo. So a forgotten prop looks exactly like a deliberate design decision, in a component
// used from seven places across three pages.
//
// That is not hypothetical: the home page kept rendering monograms for a full release after the
// logos landed, because its call site was the one the change missed, and nothing failed. The
// build passed, the types passed (Astro does not typecheck templates on build anyway), and every
// page still rendered. Only looking at it found it.
//
// Run:   node scripts/site-integration-marks.mjs

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = 'apps/site/src';
const TAG = '<IntegrationMark';

const walk = (dir) =>
  readdirSync(dir).flatMap((entry) => {
    const p = join(dir, entry);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.astro') ? [p] : [];
  });

const problems = [];

for (const file of walk(ROOT)) {
  const src = readFileSync(file, 'utf8');
  let from = 0;

  for (;;) {
    const open = src.indexOf(TAG, from);
    if (open === -1) break;

    // The props end at the tag's own close. Self-closing (`/>`) or not (`>`), whichever comes
    // first — searching only for `/>` would swallow the rest of the file on a non-self-closed tag.
    const selfClose = src.indexOf('/>', open);
    const plainClose = src.indexOf('>', open + TAG.length);
    const end = selfClose !== -1 && selfClose <= plainClose ? selfClose : plainClose;
    const props = src.slice(open, end === -1 ? src.length : end);

    if (!/\bslug\s*=/.test(props)) {
      problems.push({ file, line: src.slice(0, open).split('\n').length });
    }
    from = end === -1 ? open + TAG.length : end + 1;
  }
}

if (problems.length) {
  console.error('site:marks — <IntegrationMark> without a `slug` prop:\n');
  for (const { file, line } of problems) console.error(`  ${file}:${line}`);
  console.error(
    '\nWithout `slug` the tile silently renders the monogram instead of the vendor logo.' +
      '\nPass the integration slug, e.g. slug={i.slug}.',
  );
  process.exit(1);
}

console.log('site:marks ok — every <IntegrationMark> passes a slug');
