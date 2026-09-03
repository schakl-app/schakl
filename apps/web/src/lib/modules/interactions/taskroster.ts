/**
 * The task roster an interaction form is editing — the contact roster's twin (`roster.svelte.ts`,
 * #300), without the per-client reload: the task options already come from the link lookups the
 * host loads, and the company → project → task cascade the host runs is what narrows them.
 */

/** The shape both the API's `tasks` roster and a prefill carry. */
export interface TaskRef {
  id: string;
  title?: string | null;
}

/**
 * What a form opens on: the row's stored task roster when editing, else whatever single task the
 * host pinned when creating. `tasks` first and `task_id` only as a fallback, for the reason
 * `initialContacts` gives: a payload from an older API build carries only the lead, and opening
 * such a row on an empty roster would post one back and drop the rest on save.
 */
export function initialTasks(
  interaction: { tasks?: TaskRef[]; task_id?: string | null; task_title?: string | null } | null,
  prefill: Record<string, string | null | undefined> = {},
): TaskRef[] {
  if (interaction) {
    if (interaction.tasks?.length) return interaction.tasks;
    return interaction.task_id ? [{ id: interaction.task_id, title: interaction.task_title }] : [];
  }
  return typeof prefill.task_id === "string" && prefill.task_id ? [{ id: prefill.task_id }] : [];
}

/** Stored chips the fetched lookup did not carry (outside the first 200, or finished), as
 *  options, so an edit form keeps labelling them — the rule every host's own-link prepend follows. */
export function missingTaskOptions<T extends { value: string; label: string }>(
  stored: TaskRef[],
  fetched: T[],
  make: (ref: TaskRef) => T,
): T[] {
  const known = new Set(fetched.map((option) => option.value));
  return stored.filter((ref) => ref.title && !known.has(ref.id)).map(make);
}
