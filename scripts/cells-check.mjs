#!/usr/bin/env node
// cells:check — "a table cell either ellipsizes or says it wraps" (docs/UX.md), enforced.
//
// The mechanism, which is the whole reason this file exists rather than a note in a review
// checklist: `DataTable` lays out `table-fixed` and puts `overflow-hidden` on every `<td>`, so a
// column no longer grows to its content — anything wider **is** clipped. For an ellipsis to
// appear the content needs `truncate` *and* a box `overflow` applies to. On a bare inline
// `<span>` or `<a>`, `overflow` and `text-overflow` do not apply at all, so `truncate` sets
// `white-space: nowrap` and nothing else: the name is cut mid-glyph, silently.
//
// Both mistakes are invisible in a diff. `class="truncate"` reads as correct whichever element
// it is on, the two spellings are indistinguishable until something renders, `svelte-check` is
// happy with either, and a developer looking at a short name sees the right answer. That is the
// same shape as `forms:check` (#253) and `today:check` (#396), and it is why #370 found twenty
// cells across nine screens rather than one.
//
// Two rules:
//
//   A. `truncate` must sit on a box that can ellipsize — one carrying `block`, `inline-block`,
//      `flex`, `inline-flex`, `grid` or `w-full`, or one that is a flex/grid *item* because an
//      ancestor inside the same snippet is a flex/grid container.
//
//   B. A cell rendering free text — a name, a description, a reference, a label — truncates.
//      A cell that should wrap or overflow instead says so in a comment carrying `cells:wrap`,
//      so the decision is on the page rather than in someone's memory.
//
// Scope is deliberately narrow: `{#snippet …Cell()}` blocks in files that mount `DataTable`.
// The hand-rolled tables under `lib/modules/**` size to their content, so the mechanism does not
// apply to them and a rule about it would be noise.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = "apps/web/src";

/** Classes that make a box one `overflow` applies to, or a flex/grid item that shrinks. */
const ELLIPSIZABLE = /\b(?:block|inline-block|flex|inline-flex|grid|inline-grid|w-full)\b/;
/** A container whose children are flex/grid items. */
const CONTAINER = /\b(?:flex|inline-flex|grid|inline-grid)\b/;
/** The opt-out: this cell is meant to wrap or to overflow, and the page says why. */
const WRAP_OPT_OUT = /cells:wrap/;

/**
 * Field names that render free text a person or a tenant wrote. Not a guess about the type —
 * these are the columns #370 measured, and the ones a longer value arrives in.
 *
 * Deliberately short. A wider list (`key`, `domain`, `url`, `city`) flagged formatted numbers and
 * short slugs, and a lint whose hits are mostly noise is one that gets an `--ignore` flag and then
 * gets deleted. What is left is what is unbounded in practice: something somebody typed.
 */
const FREE_TEXT = /\b(?:\w*_?name|description|reference|label|subject|notes?|summary|remark)\b/i;

/** A value that reaches the cell through a formatter is bounded by the formatter, not the field. */
const FORMATTED = /\b(?:fmt[A-Z]\w*|money|docMoney|hours|period|capitalize\w*)\s*\(/;

function walk(dir) {
  let out = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out = out.concat(walk(path));
    else if (entry.endsWith(".svelte")) out.push(path);
  }
  return out;
}

/** Every `{#snippet nameCell(...)} … {/snippet}` block, with its offset. */
function cellSnippets(src) {
  const found = [];
  const re = /\{#snippet\s+(\w*[Cc]ell)\s*\(/g;
  for (const m of src.matchAll(re)) {
    const end = src.indexOf("{/snippet}", m.index);
    if (end === -1) continue;
    found.push({ name: m[1], start: m.index, body: src.slice(m.index, end) });
  }
  return found;
}

/**
 * Elements in `body` carrying `truncate`, each with the class lists of its open ancestors.
 *
 * A rough tag walk is enough and a parser would not be: what is being asked is only "is anything
 * still open above me a flex container", which nesting depth answers.
 */
function truncatedElements(body) {
  const stack = [];
  const found = [];
  const tag = /<(\/?)([a-zA-Z][\w-]*)((?:[^<>"']|"[^"]*"|'[^']*'|\{[^{}]*\})*?)(\/?)>/g;
  for (const m of body.matchAll(tag)) {
    const [, closing, name, attrs, selfClosing] = m;
    if (closing) {
      const at = stack.map((e) => e.name).lastIndexOf(name);
      if (at !== -1) stack.length = at;
      continue;
    }
    const classes = attrs.match(/class=(?:"([^"]*)"|\{([^{}]*)\})/)?.[1] ?? attrs;
    if (/\btruncate\b/.test(classes)) {
      found.push({
        name,
        classes,
        line: body.slice(0, m.index).split("\n").length,
        inContainer: stack.some((e) => CONTAINER.test(e.classes)),
      });
    }
    if (!selfClosing && !/^(?:br|hr|img|input|meta|link|source)$/.test(name)) {
      stack.push({ name, classes });
    }
  }
  return found;
}

let failed = false;
let cells = 0;

for (const file of walk(ROOT)) {
  const src = readFileSync(file, "utf8");
  if (!src.includes("<DataTable")) continue;

  for (const snippet of cellSnippets(src)) {
    cells++;
    const baseLine = src.slice(0, snippet.start).split("\n").length;
    const where = `${relative(".", file)}:${baseLine}`;

    // A. `truncate` on a box it cannot work on.
    for (const el of truncatedElements(snippet.body)) {
      if (ELLIPSIZABLE.test(el.classes) || el.inContainer) continue;
      failed = true;
      console.error(
        `✗ ${where} (${snippet.name}) — \`truncate\` on <${el.name}>, which is inline, so ` +
          `\`overflow\` does not apply and nothing ellipsizes: the text is cut mid-glyph. ` +
          `Add \`block\` (or \`inline-block max-w-full\`, or make it a flex item).`,
      );
    }

    // B. Free text with nothing said about it.
    if (/\btruncate\b/.test(snippet.body) || WRAP_OPT_OUT.test(snippet.body)) continue;
    const interpolations = [...snippet.body.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
    const freeText = interpolations.find(
      (expr) =>
        FREE_TEXT.test(expr) &&
        !FORMATTED.test(expr) &&
        !/^\s*[#/:@]/.test(expr) &&
        !/=>/.test(expr),
    );
    if (!freeText) continue;
    failed = true;
    console.error(
      `✗ ${where} (${snippet.name}) — free text (\`${freeText.trim().slice(0, 48)}\`) in a ` +
        `fixed-layout cell with no \`truncate\`: it is clipped with no ellipsis, or it wraps and ` +
        `makes the row taller than its neighbours. Truncate it, or say \`cells:wrap\` in a ` +
        `comment and why.`,
    );
  }
}

if (failed) {
  console.error(
    '\ncells:check failed. See docs/UX.md, "A table cell either ellipsizes or says it wraps".',
  );
  process.exit(1);
}
console.log(`cells:check ok — ${cells} table cells`);
