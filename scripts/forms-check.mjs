#!/usr/bin/env node
// forms:check — "saving must never blank the form" (docs/UX.md), enforced.
//
// The bug, in one sentence: an enhanced form that keeps SvelteKit's default success path
// calls `form.reset()`, which rewinds every control to its `defaultValue` — and a
// Svelte-managed input has none, so pressing Save empties the field you just saved and
// Svelte writes that emptiness back into the binding. It is data loss, and it has shipped
// three times: #253 (Rollen, Gebruikers → rollen, notificatiematrix), #77 (Instellingen →
// Bedrijven and → Facturatie), and again on Facturatie → verkopergegevens.
//
// The rule: a form carrying a control the user typed into must **decide** what happens to
// it on success, rather than inherit the reset by accident. Three ways to say so:
//
//   use:enhance={busy.keep(key)}                     edits what exists — never reset
//   use:enhance={busy.clear(key)}                    starts something new — empty it
//   use:enhance={busy.wrap(key, () => ({update}) =>  the mixed case, argued in place
//     update({ reset: !entry }))}
//
// `clear()` behaves exactly like a bare `use:enhance`; it exists so that emptying a form is
// something someone chose, rather than something everyone inherited.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = 'apps/web/src';

/** Components that re-assert their own state after a form reset, so they need no rule. */
const SELF_GUARDING = /<(FormCheckbox|NumberFormatField)\b/;

function walk(dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out = out.concat(walk(p));
    else if (name.endsWith('.svelte')) out.push(p);
  }
  return out;
}

/**
 * The end index of the tag opening at `start`, brace- and quote-aware: an attribute value
 * may be an arrow function spanning lines and containing `>` (`() => ({ update }) => {`),
 * so the first `>` is not the end of the tag.
 */
function tagEnd(src, start) {
  let depth = 0;
  let quote = '';
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      if (c === quote) quote = '';
      continue;
    }
    if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}') depth--;
    else if (c === '>' && depth === 0) return i;
  }
  return src.length;
}

/**
 * Has the author decided? `keep()` or any explicit `reset:` counts — including
 * `reset: !entry`, a form that creates *or* edits depending on what it was handed.
 * A form may delegate to a named `SubmitFunction`, so follow one hop into the script.
 */
function decided(tag, src) {
  if (/\.(keep|clear)\(|reset\s*:/.test(tag)) return true;
  const expr = tag.match(/use:enhance=\{([\s\S]*)$/)?.[1] ?? '';
  const name = expr.match(/^\s*([A-Za-z_$][\w$]*)\s*\}/)?.[1];
  if (!name) return false;
  const at = src.search(new RegExp(`(?:const|let|function)\\s+${name}\\b`));
  return at !== -1 && /reset\s*:|\.(keep|clear)\(/.test(src.slice(at, at + 1200));
}

/** Does this form body hold a control whose value a reset would destroy? */
function valueBearing(body) {
  // Hidden inputs are the one-shot toggle/delete shape — a reset cannot lose anything the
  // user typed, because they typed nothing.
  const visible = body.replace(/<input\b[^>]*type=["']hidden["'][^>]*>/g, '');
  const typed = /bind:(value|group|checked)|(?:^|\s)(?:value|checked|selected)=\{/.test(visible);
  if (!typed) return false;
  return !(SELF_GUARDING.test(visible) && !/bind:value|(?:^|\s)value=\{/.test(visible));
}

let failed = false;
let checked = 0;

for (const file of walk(ROOT)) {
  const src = readFileSync(file, 'utf8');
  let i = 0;
  while ((i = src.indexOf('<form', i)) !== -1) {
    const open = i;
    const end = tagEnd(src, open);
    const tag = src.slice(open, end + 1);
    i = end + 1;
    if (!/use:enhance/.test(tag)) continue;
    checked++;
    if (decided(tag, src)) continue;

    const close = src.indexOf('</form>', end);
    if (!valueBearing(src.slice(end + 1, close === -1 ? src.length : close))) continue;

    const line = src.slice(0, open).split('\n').length;
    console.error(
      `✗ ${relative('.', file)}:${line} — this form resets on save and would blank what the ` +
        `user typed. Say which you mean: \`busy.keep(key)\` to preserve it, \`busy.clear(key)\` ` +
        `to empty it for the next entry, or an explicit \`reset:\` in your own callback.`,
    );
    failed = true;
  }
}

if (failed) {
  console.error(
    '\nforms:check failed. See docs/UX.md, "Saving must never blank the form".\n' +
      'A form that edits something that already exists must not reset on success.',
  );
  process.exit(1);
}
console.log(`forms:check ok — ${checked} enhanced forms`);
