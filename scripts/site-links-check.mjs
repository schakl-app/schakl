#!/usr/bin/env node
// site:links — every internal link on the built site resolves to a page that exists.
//
// Why this exists: the Dutch docs index linked to `/docs/admin/installation/` for months.
// That path is not a page — the docs tree is symmetric `/nl/docs/…` + `/en/docs/…`, and only
// the bare `/docs` has a redirect — so three cards on the first page a Dutch reader opens went
// nowhere. Nothing caught it: `astro build` does not resolve hrefs, `docs:check` compares the
// two locale trees against each other (both were equally wrong in different ways), and a
// missing page renders as a perfectly valid <a>.
//
// So the check runs against `dist/`, after the build, where a link is either a file or it is
// not. It is deliberately dumb about anything it cannot verify — external hosts, mailto:, tel:
// — and strict about everything on this origin, including the locale prefix.
//
// Usage:  pnpm site build && node scripts/site-links-check.mjs
//         DIST=apps/site/dist node scripts/site-links-check.mjs

import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join, relative, posix } from 'node:path';

const DIST = process.env.DIST || 'apps/site/dist';

if (!existsSync(DIST)) {
  console.error(`✗ ${DIST} does not exist — run the site build first.`);
  process.exit(1);
}

function walk(dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out = out.concat(walk(p));
    else out.push(p);
  }
  return out;
}

const files = walk(DIST);
const pages = files.filter((f) => f.endsWith('.html'));

// Every path the built site can answer. A directory holding index.html answers both `/x` and
// `/x/`, so both spellings are registered — the site links to it inconsistently and both work.
const served = new Set();
for (const f of files) {
  const rel = '/' + relative(DIST, f).split(/[\\/]/).join('/');
  served.add(rel);
  if (rel.endsWith('/index.html')) {
    const dir = rel.slice(0, -'index.html'.length); // '/nl/docs/'
    served.add(dir);
    served.add(dir.replace(/\/$/, '') || '/');
  }
}

// Anchors that exist on a given page, so a `#`-link is checked too rather than assumed.
const anchorsOf = (html) => {
  const ids = new Set();
  for (const m of html.matchAll(/\sid=["']([^"']+)["']/g)) ids.add(m[1]);
  for (const m of html.matchAll(/\sname=["']([^"']+)["']/g)) ids.add(m[1]);
  return ids;
};

const anchorCache = new Map();
const anchorsForPath = (p) => {
  if (anchorCache.has(p)) return anchorCache.get(p);
  const candidates = [
    join(DIST, p.replace(/^\//, '')),
    join(DIST, p.replace(/^\//, ''), 'index.html'),
    join(DIST, p.replace(/^\//, '') + '.html'),
  ];
  const file = candidates.find((c) => existsSync(c) && statSync(c).isFile());
  const set = file ? anchorsOf(readFileSync(file, 'utf8')) : null;
  anchorCache.set(p, set);
  return set;
};

const problems = [];
let checked = 0;

for (const file of pages) {
  const from = '/' + relative(DIST, file).split(/[\\/]/).join('/');
  const html = readFileSync(file, 'utf8');

  for (const m of html.matchAll(/<a\b[^>]*?\shref=["']([^"']*)["']/gi)) {
    const raw = m[1].trim();
    if (!raw) continue;
    // Not ours to verify.
    if (/^(https?:|mailto:|tel:|javascript:|data:|#)/i.test(raw)) continue;
    if (!raw.startsWith('/')) continue; // relative links: Starlight emits none, skip rather than guess

    checked++;
    const [pathPart, hash] = raw.split('#');
    const path = pathPart.split('?')[0];

    const hit =
      served.has(path) ||
      served.has(path.replace(/\/$/, '')) ||
      served.has(path + '/') ||
      served.has(path + '/index.html') ||
      served.has(path + '.html');

    if (!hit) {
      problems.push(`${from} → ${raw}  (no such page)`);
      continue;
    }
    if (hash) {
      const target = path.endsWith('/') ? path : path + '/';
      const ids = anchorsForPath(target) ?? anchorsForPath(path);
      if (ids && ids.size && !ids.has(hash)) {
        problems.push(`${from} → ${raw}  (page exists, #${hash} does not)`);
      }
    }
  }
}

// A locale-prefix mistake is the failure this file was written for, so name it as itself
// rather than leaving it inside the generic "no such page" pile.
const localeSlips = problems.filter((p) => / → \/docs\//.test(p));

if (problems.length) {
  console.error(`✗ ${problems.length} broken internal link(s) of ${checked} checked:\n`);
  for (const p of problems) console.error(`  ${p}`);
  if (localeSlips.length) {
    console.error(
      `\n  ${localeSlips.length} of these point at /docs/… — docs pages live under ` +
        `/nl/docs/… and /en/docs/…; only the bare /docs redirects.`,
    );
  }
  process.exit(1);
}

console.log(`site:links ok — ${checked} internal links across ${pages.length} pages all resolve.`);
