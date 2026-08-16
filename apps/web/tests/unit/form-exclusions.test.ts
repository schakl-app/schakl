/**
 * Turning a list of ticked checkboxes into a list of *exclusions* (`$lib/core/forms.ts`).
 *
 * A client with several websites decides which of them their report covers by ticking them
 * (#381). The obvious implementation — store the ticked ids — is wrong twice, and both failures
 * are silent on the screen that caused them:
 *
 * - a checkbox list only posts what was **rendered**, so a row behind an `{#if}`, a permission
 *   or a page boundary reads as unticked and is quietly dropped;
 * - and a property linked *later* would default to excluded, which is backwards — linking a
 *   property to a client is how somebody says they want it in the report.
 *
 * So the form carries every candidate id in a hidden field and the diff is taken against that.
 * None of this is visible in a screenshot, and all of it is obvious here.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { excludedFrom } from "../../src/lib/core/forms.ts";

describe("excludedFrom", () => {
  test("what was rendered and not ticked is what is excluded", () => {
    assert.deepEqual(excludedFrom("a,b,c", ["a", "c"]), ["b"]);
  });

  test("everything ticked excludes nothing", () => {
    assert.deepEqual(excludedFrom("a,b", ["a", "b"]), []);
  });

  test("nothing ticked excludes everything that was on the screen", () => {
    // A real answer, not an error: an agency may genuinely report on none of a client's
    // properties for a month. The *caller* decides whether that is worth storing.
    assert.deepEqual(excludedFrom("a,b", []), ["a", "b"]);
  });

  test("an empty field means the form said nothing, not that everything is excluded", () => {
    // The block is only rendered for a client with more than one property. Reading its absence
    // as "exclude nothing from nothing" is what keeps a screen that does not draw the control
    // from silently rewriting the stored setting.
    assert.deepEqual(excludedFrom("", ["a"]), []);
  });

  test("an id posted that was never rendered is ignored", () => {
    // The authority on what existed is the field the server wrote, not one a client can post.
    assert.deepEqual(excludedFrom("a,b", ["a", "b", "z"]), []);
  });

  test("whitespace and empty segments are not ids", () => {
    // `[...].join(",")` over an empty list is "", and a trailing comma is one bad concatenation
    // away — neither should ever become an exclusion for a link called "".
    assert.deepEqual(excludedFrom(" a , b ,,", ["a"]), ["b"]);
  });

  test("the posted values are read as strings, whatever FormData handed back", () => {
    // `form.getAll` is typed `FormDataEntryValue[]`, which is `string | File`.
    assert.deepEqual(excludedFrom("a,b", ["a" as unknown as FormDataEntryValue]), ["b"]);
  });
});
