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
