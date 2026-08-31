/**
 * Whether an hour entry bills — and, the harder half, whether *the person has said so yet*.
 *
 * The rule itself is one line (#284): left alone, a new entry bills the way its project bills,
 * and a project a subscription covers bills *not*, because the retainer already pays for that
 * work. The API resolves exactly this when a client sends no `billable` at all; the form only
 * shows the answer up front. What the form adds is the other half — once somebody has moved the
 * toggle themselves, switching projects must never overrule them.
 *
 * That second half is where it went wrong, and it went wrong invisibly. "The person settled it"
 * was read off the restored draft as *`billable` is present*, and a draft (#44) always carries
 * `billable`, because the payload writes every field on every autosave. So any day with a
 * concept on it — one typed word is enough to save one — opened its form permanently frozen at
 * whatever the draft happened to hold, and picking a non-billable project after that left the
 * entry billable, silently, in the exact field an invoice is built from. The screen says nothing:
 * the toggle is a real control showing a real value, and only the project's own settings page
 * disagrees with it.
 *
 * So a draft records the *decision* (`billable_touched`) beside the value, and a draft saved
 * before that field existed is read the only honest way left: a value that differs from what its
 * own project would have seeded is a decision, and a value that agrees with it is not. That
 * fallback loses nothing — re-seeding a value equal to the project's default answers the same
 * thing the draft held, right up until the project changes, which is precisely when the project
 * should answer.
 *
 * Kept out of `EntryForm.svelte` so `tests/unit/time-billable.test.ts` can hold it to those
 * rules: which of them is wrong is not something you can see by opening a form.
 */

/** Just enough of a project option to answer what it bills. */
export interface BillableProject {
  id: string;
  /** Absent (an older lookup, a caller that trimmed it) means the platform default: bills. */
  billable_default?: boolean;
}

/** What a restored draft says about the flag: the value, the decision, and the project it was
 *  decided under. */
export interface RestoredBillable {
  billable?: boolean | null;
  /** Whether the person moved the toggle themselves. Absent on a draft saved before #284's fix. */
  billable_touched?: boolean | null;
  project_id?: string | null;
}

/** What a new entry on this project bills by default. No project means the plain `true` the API
 *  answers for an entry attached to none. */
export function projectBillableDefault(
  projects: readonly BillableProject[],
  projectId: string | null | undefined,
): boolean {
  if (!projectId) return true;
  return projects.find((p) => p.id === projectId)?.billable_default ?? true;
}

/**
 * Has the person themselves decided this entry's billable flag?
 *
 * `true` freezes the toggle against the project cascade; `false` leaves the project answering.
 * Presence of a value is deliberately *not* the question — see the note at the top of the file.
 */
export function billableSettled(
  restored: RestoredBillable | null | undefined,
  projects: readonly BillableProject[],
): boolean {
  if (!restored || restored.billable == null) return false;
  if (restored.billable_touched != null) return Boolean(restored.billable_touched);
  return restored.billable !== projectBillableDefault(projects, restored.project_id);
}
