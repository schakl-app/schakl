/**
 * The roster that `AssigneePicker` posts, decoded for a form action.
 *
 * The picker serialises the whole roster into one hidden field, so a form that doesn't render it
 * (a quick-create dialog, say) simply omits the field — and `undefined` tells the API "I didn't
 * say", which is not the same as `[]` ("nobody"). Never send a guess.
 */
export interface Assignee {
  user_id: string;
  is_primary: boolean;
}

export function parseAssignees(raw: FormDataEntryValue | null): Assignee[] | undefined {
  if (raw == null) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(String(raw));
  } catch {
    return undefined;
  }
  if (!Array.isArray(parsed)) return undefined;
  return parsed
    .filter((entry): entry is Assignee => Boolean(entry) && typeof entry === "object")
    .map((entry) => ({ user_id: String(entry.user_id), is_primary: Boolean(entry.is_primary) }))
    .filter((entry) => entry.user_id.length > 0);
}
