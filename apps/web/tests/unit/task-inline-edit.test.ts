/**
 * The task detail page has one mode, and a prompt that confirms is a prompt that saves.
 *
 * Edit mode on this page — title, status, dates, priority, relations, visibility and planning
 * joined to one `form="task-edit"` with an Opslaan at the foot — is gone by decision: every
 * field is edited where it is read (`InlineField`, `InlineText`, the title by clicking it), and
 * a change is one field with its own save. Two rules that a browser cannot show you are pinned
 * against the source text here, because each one is a line that would read as correct in review.
 *
 * 1. **No second save model.** A `form="task-edit"` join, an `editMode` flag or an `?edit=1`
 *    intent creeping back in is the whole-page mode returning one field at a time.
 * 2. **Confirming the deadline reason submits the field.** The prompt used to stage the reason
 *    and hand the user back to a date with an Opslaan still to press, which read as the confirm
 *    having done nothing. Bevestigen posts the date and the reason together.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

const PAGE = "routes/(app)/tasks/[id]/+page.svelte";
const source = readFileSync(new URL(`../../src/${PAGE}`, import.meta.url), "utf8");

/** A function's body, from its declaration to the closing brace at the same depth. */
function body(name: string): string {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name}() exists`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unterminated ${name}()`);
}

describe("the task detail page edits in place", () => {
  test("there is no page-wide edit mode", () => {
    assert.doesNotMatch(source, /\beditMode\b/);
    assert.doesNotMatch(source, /form="task-edit"/);
    assert.doesNotMatch(source, /editIntent|clearEditIntent/);
  });

  test("the title is edited where it is read", () => {
    // Its own small form posting `title` alone, opened by clicking the heading.
    assert.match(source, /onclick=\{startTitleEdit\}/);
    assert.match(source, /name="title"[\s\S]{0,400}onkeydown=\{titleKeydown\}/);
  });

  test("confirming the deadline reason is the save", () => {
    const confirm = body("confirmDueReason");
    // The reason is staged for the hidden field, the prompt closes, and the editor's own
    // submit is pressed — next tick, so the hidden input carries the reason.
    assert.match(confirm, /dueReason = reasonDraft\.trim\(\)/);
    assert.match(confirm, /reasonModalOpen = false/);
    assert.match(confirm, /submit\?\.\(\)/);
  });

  test("a deadline moved earlier saves on pick, with no reason asked", () => {
    const changed = body("onDueChanged");
    // Next tick: `DateInput` calls back before the picked value is in the hidden input the
    // form posts, so a same-tick submit saved the date the field already had.
    assert.match(changed, /void tick\(\)\.then\(submit\);\s*\}\s*$/);
    // The field draws no Opslaan that would repeat the pick.
    assert.match(source, /id="due_date"[\s\S]{0,300}saveOnChange/);
  });

  test("keeping the old date is the only other way out of the prompt", () => {
    const keep = body("keepOldDue");
    assert.match(keep, /dueValue = task\.due_date \?\? ""/);
    assert.match(keep, /reasonModalOpen = false/);
    // …and it closes the editor: with the old date back there is nothing left in it to save.
    assert.match(keep, /cancel\?\.\(\)/);
    assert.match(source, /closeGuard=\{\(\) => \{\s*keepOldDue\(\);/);
  });

  test("an extension is never posted without its reason", () => {
    // Enter in the date box submits the form implicitly, before the prompt has its answer; the
    // field refuses that submit rather than letting the API print "reason required" under it.
    assert.match(
      source,
      /id="due_date"[\s\S]{0,1500}beforeSubmit=\{[\s\S]{0,900}return !\(extends_ && !reason\)/,
    );
  });

  test("a one-control editor closes when focus leaves it, and only those", () => {
    // The select and checkbox fields dismiss on blur and draw no Annuleren; the deadline must
    // not, because its prompt takes focus and that would read as leaving.
    for (const id of ["status", "priority", "visible_to_client", "requires_interaction"]) {
      assert.match(source, new RegExp(`id="${id}"[\\s\\S]{0,200}dismissOnBlur`), id);
    }
    assert.doesNotMatch(source, /id="due_date"[\s\S]{0,300}dismissOnBlur/);
  });
});

describe("InlineField saves once per gesture", () => {
  const field = readFileSync(
    new URL("../../src/lib/core/ui/InlineField.svelte", import.meta.url),
    "utf8",
  );
  const dateInput = readFileSync(
    new URL("../../src/lib/core/ui/DateInput.svelte", import.meta.url),
    "utf8",
  );

  test("a submit while one is in flight is dropped", () => {
    // Enter in a text input commits the value *and* submits the form implicitly; the second
    // request wrote the same change again, one trail line per copy.
    assert.match(
      field,
      /if \(busy\.active \|\| \(beforeSubmit && !beforeSubmit\(formData\)\)\) \{\s*cancel\(\);/,
    );
  });

  test("DateInput announces a change only when the value moved", () => {
    assert.match(
      dateInput,
      /function commit\(iso: string\) \{[\s\S]{0,400}if \(iso === value\) return;[\s\S]{0,100}onchange\?\.\(iso\);/,
    );
  });

  test("dismissOnBlur closes on focusout unless a save is on its way", () => {
    assert.match(
      field,
      /function onfocusout\(event: FocusEvent\) \{\s*if \(!dismissOnBlur \|\| busy\.active\) return;/,
    );
    assert.match(
      field,
      /\{#if !dismissOnBlur\}\s*<div class="flex items-center justify-end gap-2">/,
    );
  });

  test("a finishing status pick leaves the page rather than reloading the field", () => {
    assert.match(source, /statusFinishing = finishes\(formData\.get\("status"\)\)/);
    assert.match(
      source,
      /if \(!statusFinishing\) return true;\s*leaveFinished\(\);\s*return false;/,
    );
  });
});
