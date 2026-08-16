/**
 * Reading a posted form, where the reading is easy to get silently wrong.
 *
 * **A checkbox posts its `value`, and an unticked one posts nothing at all.** That is the whole
 * rule, and every way of writing it down that names a *particular* value is a bug waiting for
 * somebody to change the control. `FormCheckbox` sends `value="true"`; a bare
 * `<input type="checkbox">` sends `"on"`; a control that ever grows an explicit `value` sends
 * that. Reporting's forms compared against `"on"` while drawing `FormCheckbox`, so **every
 * checkbox in the module was posted as `false` whatever the user ticked** — the "standaard"
 * mark on a report template never stuck, which is why no template was ever the default, which
 * is why generated reports ignored the design, the accent and the cover photograph the tenant
 * had uploaded. The same line silently switched a client's reporting profile to inactive on
 * every save.
 *
 * The failure is invisible in review (the string looks plausible) and invisible in use (the box
 * is ticked on screen; only the next page load says otherwise), so the fix is a helper rather
 * than a corrected literal: presence *is* the question, and asking it any other way is the bug.
 */

/** Whether a checkbox was ticked — presence, never a particular posted value. */
export function checked(form: FormData, name: string): boolean {
  return form.get(name) !== null;
}

/**
 * A three-way flag: `true` / `false` / `null` for *inherit*.
 *
 * A checkbox cannot express this and should not be asked to. A record that says nothing follows
 * the org default (§14's `NULL` = inherit idiom), and "I did not choose" has to be visibly
 * different from "I chose off" — so the control is a select whose empty option is inherit, the
 * same shape the cadence and delivery fields beside it already use.
 */
export function triflag(form: FormData, name: string): boolean | null {
  const raw = String(form.get(name) ?? "").trim();
  return raw === "" ? null : raw === "true";
}

/**
 * The rows a checkbox list leaves **out**, from the rows it was rendered with.
 *
 * The obvious version — take the ticked boxes and store those — is wrong twice over, and both
 * failures are silent. A checkbox list only posts what was rendered, so a row filtered out of
 * the loop (a permission, a `{#if}`, a page of results) reads as *unticked* and is quietly
 * dropped: the `bind:group` trap, one layer out. And storing the ticked set makes a row added
 * later default to *excluded*, which is backwards — somebody who links a new property to a
 * client means it to appear in their report.
 *
 * So the caller renders every candidate id into a hidden field, and this answers the diff. Two
 * consequences worth stating: an empty `all` means "this form had nothing to say about it" and
 * the caller must leave the stored value alone rather than clearing it, and an id in `ticked`
 * that is not in `all` is ignored, because the authority on what existed is the field the
 * server wrote, not one the client can post.
 */
export function excludedFrom(all: string, ticked: FormDataEntryValue[]): string[] {
  const kept = new Set(ticked.map(String));
  return all
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean)
    .filter((id) => !kept.has(id));
}
