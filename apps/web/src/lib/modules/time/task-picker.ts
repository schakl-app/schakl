/**
 * Which tasks the hour-entry picker offers, and what each one says about itself.
 *
 * Hours are logged on work that is still going on, so the opening list is the org's *open*
 * tasks. Finished ones are not removed — people do log a forgotten hour against a task closed
 * last week, and a picker that cannot name it sends them to another screen — they are moved
 * behind a search (`Combobox`'s `archived` bucket) and labelled with the status they are in.
 *
 * Which statuses mean finished is the tenant's own vocabulary (#62), never a key called `done`,
 * so an empty `statuses` means *unknown* and everything stays on offer. Formatting is injected:
 * a date is rendered in the user's chosen order and an i18n string is not this module's to hold.
 *
 * Kept out of `EntryForm.svelte` so `tests/unit/time-task-picker.test.ts` can hold it to these
 * rules: which of them is wrong is not a thing you can see by opening the dropdown.
 */

export interface PickerTask {
  id: string;
  title?: string;
  project_id?: string | null;
  allocated_minutes?: number | null;
  /** The task's status key, matched against the org's vocabulary. */
  status?: string | null;
  due_date?: string | null;
}

export interface PickerStatus {
  key: string;
  name: string;
  is_terminal: boolean;
}

export interface PickerOption {
  value: string;
  label: string;
  hint?: string;
}

export interface PickerLabels {
  /** e.g. `(d) => "deadline 07-07-2026"` — the host owns the wording and the date order. */
  due: (isoDate: string) => string;
  /** e.g. `(m) => "2u 30m"`. */
  allocated: (minutes: number) => string;
}

export interface SplitOptions {
  /** The picked project, if any: narrows the list the way the form's cascade does. */
  projectId?: string;
  /** The task this entry is already on. Always offered, whatever status it ended up in. */
  selectedId?: string;
  statuses: PickerStatus[];
  labels: PickerLabels;
}

/**
 * `open` for the dropdown, `closed` for the search-only bucket underneath it.
 *
 * Order is preserved from `tasks` in both — the caller sorted the lookup, and re-sorting a
 * picker's opening list is a second opinion about a question the API already answered.
 */
export function splitTaskOptions(
  tasks: PickerTask[],
  { projectId = "", selectedId = "", statuses, labels }: SplitOptions,
): { open: PickerOption[]; closed: PickerOption[] } {
  const terminal = new Set(
    statuses.filter((status) => status.is_terminal).map((status) => status.key),
  );
  const statusName = new Map(statuses.map((status) => [status.key, status.name]));

  /**
   * The picker's hint: the tenant's own name for a finished status, then the deadline, then the
   * hour allocation. The deadline earns its place because "Nieuwsbrief" is four different tasks
   * in a busy month and the date is the only thing that tells them apart. `Combobox` searches
   * hints as well as labels, so it is also what makes typing "Gereed" list what is finished.
   */
  function hint(task: PickerTask): string | undefined {
    const parts = [
      task.status && terminal.has(task.status) ? statusName.get(task.status) : undefined,
      task.due_date ? labels.due(task.due_date) : undefined,
      task.allocated_minutes ? labels.allocated(task.allocated_minutes) : undefined,
    ].filter((part): part is string => Boolean(part));
    return parts.length > 0 ? parts.join(" · ") : undefined;
  }

  const open: PickerOption[] = [];
  const closed: PickerOption[] = [];
  for (const task of tasks) {
    // A task with no project belongs to no project in particular, so it stays offered under
    // every one of them — the same rule the client/project pickers above it follow.
    if (projectId && task.project_id && task.project_id !== projectId) continue;
    const option = { value: task.id, label: task.title ?? "", hint: hint(task) };
    // Closing a task after logging against it is the normal order of events, not a mistake to
    // hide: the entry being edited must be able to show what it is booked on.
    const finished = task.id !== selectedId && Boolean(task.status) && terminal.has(task.status!);
    (finished ? closed : open).push(option);
  }
  return { open, closed };
}
