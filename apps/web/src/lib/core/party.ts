/**
 * The universal responsible "party" (issue #88), mirrored on the web.
 *
 * A party resolves to the agency, a client company, an employee or a contact. The picker
 * serialises `{ type, id }` into one hidden form field; `parseParty` decodes it in a server
 * action, exactly as `parseAssignees` does for the assignee roster.
 */

export type PartyType = "agency" | "company" | "employee" | "contact";

export interface PartyRef {
  type: PartyType;
  id: string | null;
}

export interface PartyReadRef {
  type: PartyType;
  id: string | null;
  label: string;
}

const TYPES: readonly PartyType[] = ["agency", "company", "employee", "contact"];

/** Decode the JSON the picker posts. Returns `null` when the field is absent or empty. */
export function parseParty(raw: FormDataEntryValue | null): PartyRef | null {
  if (raw == null) return null;
  const text = String(raw).trim();
  if (!text) return null;
  try {
    const value = JSON.parse(text) as { type?: string; id?: string | null };
    if (value && typeof value === "object" && TYPES.includes(value.type as PartyType)) {
      return { type: value.type as PartyType, id: value.id ?? null };
    }
  } catch {
    /* fall through */
  }
  return null;
}
