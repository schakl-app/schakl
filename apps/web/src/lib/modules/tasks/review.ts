/**
 * How the review slide-over takes in a run that landed while it was open (`TaskReviewDialog`).
 *
 * The dialog is a form over a fetched row, and "schakl vult deze taak in" (#327) can finish
 * while the reader has that form open. The rule is the one the dialog states: **the server's
 * answer for every field the reader left alone, theirs for every field they touched** — and
 * for the one field that can be half-typed, the description, the reader may press a button
 * that *merges* what the run added under their own words rather than replacing them.
 *
 * Pure, and kept out of the component for the reason it was found broken: the merge is a
 * comparison against a *baseline*, and the first (unforced) reveal used to advance that
 * baseline to the server's text even for the field it had refused to touch — so the button it
 * offered a second later compared the same server text against itself, found nothing added,
 * disappeared, and left the reader's note exactly as it was. A field that was not adopted keeps
 * its baseline until it is, which is the whole fix, and a rule about three states of one string
 * is a rule for a test rather than a component.
 */

export interface ReviewFields {
  title: string;
  description: string;
  project_id: string;
  due_date: string;
}

export interface RevealOutcome {
  /** The form's values after taking the run in. */
  form: ReviewFields;
  /** What the next comparison starts from. */
  baseline: ReviewFields;
  /** Whether everything the run wrote is now on screen — `false` keeps the button. */
  shown: boolean;
  /** The description editor is mounted, so a new value is a remount, never a prop change. */
  remountDescription: boolean;
}

/** The separator `tasks/system.apply_ai_enrichment_system` appends a description under. */
const APPENDED_RULE = /^\s*---\s*/;

/**
 * Fold the row the server now holds into the form.
 *
 * `forced` is the button: the reader asked, so a description they were typing in is merged
 * rather than left alone. Without it a touched description stays theirs and `shown` is
 * `false`, and — load-bearing — its baseline stays where it was, so that a later forced reveal
 * can still tell what the run added.
 */
export function adoptRun(
  form: ReviewFields,
  baseline: ReviewFields,
  server: ReviewFields,
  forced: boolean,
): RevealOutcome {
  const next: ReviewFields = { ...form };
  const nextBaseline: ReviewFields = { ...server };
  let shown = true;
  let remountDescription = false;

  if (form.description === baseline.description) {
    next.description = server.description;
    remountDescription = true;
  } else if (server.description && server.description !== baseline.description) {
    if (forced) {
      // What the run added is the part past what the form started from: it appends under a
      // rule, and the reader's own words go first.
      const added = server.description.startsWith(baseline.description)
        ? server.description.slice(baseline.description.length).replace(APPENDED_RULE, "")
        : server.description;
      next.description = added.trim()
        ? `${form.description.trim()}\n\n${added.trim()}`
        : form.description;
      remountDescription = true;
    } else {
      shown = false;
      nextBaseline.description = baseline.description;
    }
  }

  if (form.title === baseline.title) next.title = server.title;
  if (form.project_id === baseline.project_id) next.project_id = server.project_id;
  if (form.due_date === baseline.due_date) next.due_date = server.due_date;

  return { form: next, baseline: nextBaseline, shown, remountDescription };
}
