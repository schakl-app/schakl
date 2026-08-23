/**
 * The control that ends the task detail page's edit mode commits it (#409).
 *
 * Edit mode there is the *whole page* — title, status, dates, priority, client/project,
 * visibility, planning — joined to one `form="task-edit"` whose Opslaan sits at the foot. The ⋯
 * at the top offered "Klaar met bewerken", and it only flipped `editMode` back: a second
 * Annuleren under the opposite word. Nothing failed, so nothing was reported — the page left
 * edit mode, the header showed the stored title again, and the user found out the next day from
 * a task that still said what it said yesterday.
 *
 * This is asserted against the source text because a browser cannot show you the difference.
 * Both shapes leave edit mode and re-render the stored record; only a reload, later, tells them
 * apart. A regression here is one line and would read as correct in review, which is exactly the
 * kind of rule docs/UX.md says to pin rather than remember.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

const PAGE = "routes/(app)/tasks/[id]/+page.svelte";
const source = readFileSync(new URL(`../../src/${PAGE}`, import.meta.url), "utf8");

/** The ⋯ item that enters and leaves edit mode, from its label to the end of its `onclick`. */
function exitItem(): string {
  const start = source.indexOf('label: editMode ? t("tasks.detail.done_editing")');
  assert.notEqual(start, -1, "the ⋯ still offers an edit-mode toggle");
  const end = source.indexOf("},", source.indexOf("onclick:", start));
  assert.notEqual(end, -1);
  return source.slice(start, end);
}

describe("the task detail page's edit-mode exit", () => {
  test("Klaar met bewerken submits the edit form", () => {
    assert.match(exitItem(), /editForm\?\.requestSubmit\(\)/);
  });

  test("it never leaves edit mode by flipping the flag", () => {
    // `editMode = !editMode` and `editMode = false` are both the bug: the first is what shipped,
    // the second is the same discard written out. Entering is the only assignment this item makes.
    const item = exitItem();
    assert.doesNotMatch(item, /editMode = !editMode/);
    assert.doesNotMatch(item, /editMode = false/);
    assert.match(item, /editMode = true/);
  });

  test("it submits rather than posts, so `required` is checked and enhance runs", () => {
    // `submit()` bypasses constraint validation *and* `use:enhance`, which would post the form
    // outside the handler that decides whether edit mode may close — a title cleared to nothing
    // would leave the page on a full reload instead of showing the field's own error.
    assert.doesNotMatch(exitItem(), /editForm\?\.submit\(\)/);
  });

  test("discarding is still offered, and is still called Annuleren", () => {
    // The fix must not remove the honest exit: a user who wants their edits gone needs a control
    // that says so, and there must be exactly one of it.
    //
    // Asserted through the *handler* rather than by proximity, because the discard has moved
    // twice since this was written and both moves left the rule intact: #402 gave the button a
    // block body (it consumes the edit-intent marker too), and #408 lifted the whole thing into
    // `leaveEdit()` near the top of the file, ~1700 lines from the button that calls it. A
    // `slice(cancel, cancel + 300)` was pinning where the code happened to sit.
    assert.equal(
      source.split("editMode = false").length - 1,
      1,
      "exactly one control discards",
    );
    const discard = source.indexOf("editMode = false");
    assert.ok(
      source.slice(0, discard).includes("function leaveEdit"),
      "the discard lives in leaveEdit()",
    );
    assert.match(
      source,
      /onclick=\{leaveEdit\}[\s\S]{0,300}t\("common\.cancel"\)/,
      "and leaveEdit() is what the Annuleren button calls",
    );
  });

  test("a successful save closes edit mode; a failed one keeps the work on the screen", () => {
    // The whole fix rests on this handler: `pendingSave` is null for an ordinary save (including
    // this one), so success closes edit mode, and a `failure` result changes nothing — the user
    // stays in the form with the error rather than losing what they typed.
    assert.match(source, /if \(result\.type === "success"\) \{\s*editMode = waiting !== null;/);
  });
});
