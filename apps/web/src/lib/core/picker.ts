/**
 * Splitting a picker's options into what is still live and what has been retired.
 *
 * `Combobox`'s `archived` bucket already knows what to *do* with a retired option — keep it out
 * of the opening list, find it once the user types, never rank it above a live one. What it
 * cannot know is which options those are: that is each module's own lifecycle vocabulary. This
 * is the one place that decision is expressed, so the twenty-odd pickers that point at clients,
 * projects and tasks cannot end up with twenty answers to "is this one still going on?".
 *
 * Two rules are worth stating because both were got wrong before there was a helper:
 *
 * 1. **The picked row is always live.** A time entry booked on a finished project, an invoice
 *    addressed to a client since archived — the field has to be able to show what is in it, and
 *    a value that resolves to nothing reads as an empty box, not as "this is archived".
 * 2. **A status is said out loud, not implied by its bucket.** Everything outside `quiet` gets
 *    its status as the option's hint, so a paused project says "On hold" while it is still on
 *    offer. A row the user cannot tell apart from a live one is the bug this exists to fix;
 *    dropping it from the list is only half the answer, and on its own the more confusing half.
 *
 * The hint is also what makes a retired row *findable*: `Combobox` searches hints as well as
 * labels, so typing "Gearchiveerd" lists the archive.
 */

export interface PickerOption {
  value: string;
  label: string;
  hint?: string;
}

/** An option that knows its own lifecycle status. */
export interface StatusedOption extends PickerOption {
  status?: string | null;
}

/**
 * One module's lifecycle vocabulary, in the shape a core component can be handed.
 *
 * Core holds none of it: which statuses retire a row, which need no saying, what each is called
 * and what the bucket's heading reads are all the owning module's answers. A shared picker
 * (`PartyPicker`) therefore *takes* this rather than importing a module — the same reason the
 * `Combobox` takes `archivedLabel` instead of holding a word.
 */
export interface LifecycleVocabulary {
  /** Status keys that mean "no longer running" — moved behind the search. */
  retired: readonly string[];
  /** Status keys that need no saying. Everything else is named in the option's hint. */
  quiet: readonly string[];
  /** The user-facing name of a status key. */
  statusLabel: (status: string) => string;
  /** The heading `Combobox` draws above the search-only rows. */
  archivedLabel: string;
}

export interface LifecycleRules extends Omit<LifecycleVocabulary, "archivedLabel"> {
  /**
   * The value(s) currently held by the field(s) this split feeds. Always offered, whatever
   * status they ended up in.
   *
   * A list may accept several because one lookup often feeds more than one control on a screen
   * — a filter above the table and a picker inside its dialog — and splitting the same rows
   * twice to satisfy two selections would be two answers to one question.
   */
  selectedId?: string | readonly (string | null | undefined)[];
}

export interface LifecycleSplit {
  /** The opening list, in the order the caller supplied. */
  live: PickerOption[];
  /** Search-only, shown under the picker's `archivedLabel`. */
  retired: PickerOption[];
}

/**
 * Order is preserved from `options` in both buckets: the caller sorted the lookup (or the API
 * did), and re-sorting a picker's opening list is a second opinion about a question already
 * answered.
 */
export function splitLifecycle(
  options: readonly StatusedOption[],
  { retired, quiet, statusLabel, selectedId = [] }: LifecycleRules,
): LifecycleSplit {
  const retiredSet = new Set(retired);
  const quietSet = new Set(quiet);
  const kept = new Set(
    (typeof selectedId === "string" ? [selectedId] : selectedId).filter(Boolean) as string[],
  );
  const live: PickerOption[] = [];
  const gone: PickerOption[] = [];
  for (const option of options) {
    const status = option.status ?? "";
    const named = status && !quietSet.has(status) ? statusLabel(status) : undefined;
    // The module's own hint (a deadline, a client name) keeps its place; the status joins it
    // rather than replacing it, and leads, because it is the thing being flagged.
    const hint = [named, option.hint].filter(Boolean).join(" · ") || undefined;
    const entry: PickerOption = { value: option.value, label: option.label, hint };
    (retiredSet.has(status) && !kept.has(option.value) ? gone : live).push(entry);
  }
  return { live, retired: gone };
}
