#!/usr/bin/env node
// today:check — "no module keeps its own clock" (CLAUDE.md §8), enforced in the web app.
//
// The bug, in one sentence: `new Date().toISOString().slice(0, 10)` is not the org's calendar
// date and not even the viewer's, it is **UTC's** — so in `Europe/Amsterdam` every date built
// that way names *yesterday* between midnight and 02:00, and in a `+page.server.ts` it names
// the wrong day all day, because the shipped container's `TZ` is UTC.
//
// It shipped in twenty-nine places at once (#396), including every overdue marker in the app, and
// nothing could catch it: the types agree, `svelte-check` is happy, and the value is a
// perfectly well-formed `YYYY-MM-DD`. It is only wrong on a clock nobody is watching. That is
// the same shape as the two lints this repo already runs — `forms:check` and `i18n:check` — and
// the reason for a third.
//
// The rule: **reading the current date or year goes through `$lib/core/today.ts`.**
//
//   orgToday()   today's calendar date in the tenant's zone, as `YYYY-MM-DD`
//   orgYear()    the calendar year it is in the tenant's zone
//   todayIn(zone, now)   the pure half, for tests
//
// What this refuses is a **clock read**, by shape and never by filename: the offending call
// starts at `new Date()` / `Date.now()` with no argument. Converting a value you were handed is
// a different act and stays legal — `calendar.ts` stepping an ISO day, `DateInput` validating
// typed digits, `notifications/format.ts` walking back one `Date.UTC(...)` day. Those take their
// instant from a parameter, so there is no clock in them to get wrong.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = 'apps/web/src';
// The one file allowed to ask what time it is and turn it into a calendar date.
const HOME = join(ROOT, 'lib/core/today.ts');

/**
 * A clock read that becomes a date or a year.
 *
 * `new Date()` with **no argument** (a parenthesised argument list that is empty), or
 * `Date.now()`, followed by the shapes that turn it into a calendar value. An argument makes it
 * a conversion, which is exactly the distinction this check is built on.
 */
const PATTERNS = [
  // new Date().toISOString().slice(0, 10)  /  .slice(0, 7)  /  .slice(0, 8)
  [/new Date\(\s*\)\s*\.toISOString\(\)\s*\.slice\(/g, 'orgToday()'],
  // new Date().getFullYear() / .getUTCFullYear() / .getMonth() / .getDate() / …
  [/new Date\(\s*\)\s*\.get(?:UTC)?(?:FullYear|Month|Date|Day)\(\)/g, 'orgYear() (or orgToday(), sliced)'],
  // new Date(Date.now()).toISOString().slice(…) — the same read wearing an argument
  [/new Date\(\s*Date\.now\(\)\s*\)\s*\.toISOString\(\)\s*\.slice\(/g, 'orgToday()'],
];

function walk(dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (name === 'paraglide' || name === 'node_modules') continue;
    if (statSync(p).isDirectory()) out = out.concat(walk(p));
    else if (name.endsWith('.ts') || name.endsWith('.svelte')) out.push(p);
  }
  return out;
}

/** Strip block and line comments, so the prose in `today.ts` and its callers cannot trip this. */
function code(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/\/\/[^\n]*/g, '');
}

let failed = false;
let scanned = 0;

for (const file of walk(ROOT)) {
  if (file === HOME) continue;
  const src = code(readFileSync(file, 'utf8'));
  scanned++;
  for (const [pattern, suggestion] of PATTERNS) {
    for (const match of src.matchAll(pattern)) {
      const line = src.slice(0, match.index).split('\n').length;
      console.error(
        `✗ ${relative('.', file)}:${line} — \`${match[0].replace(/\s+/g, '')}\` reads a clock ` +
          `that is UTC's, not the tenant's. Use \`${suggestion}\` from \`$lib/core/today\`.`,
      );
      failed = true;
    }
  }
}

if (failed) {
  console.error(
    "\ntoday:check failed. See CLAUDE.md §8 (\"no module keeps its own clock\") and " +
      'apps/web/src/lib/core/today.ts.\n' +
      'Converting a value you were handed is fine — this only refuses reading the current ' +
      'date or year without the tenant\'s zone.',
  );
  process.exit(1);
}
console.log(`today:check ok — ${scanned} files`);
