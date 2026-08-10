/**
 * How a member is written down when a screen has to name one.
 *
 * The chain used to be spelled `m.full_name || m.email` at every call site, which worked only
 * because `/members/lookup` always answered with an address. It no longer does: an **external
 * (client) login** gets the names its own screens draw and not the agency's address book, so
 * `email` is `null` there. That makes the old expression `string | null` — a label type nothing
 * accepts, and a fallback that would have printed "null" if anything had.
 *
 * So the rule is stated once and ends in the empty string: a colleague who has not set a name yet
 * reads as blank for a client, never as somebody's mailbox. Staff are unaffected — they still get
 * the address, and it is still the second choice rather than the first.
 */
export interface NamedMember {
  full_name?: string | null;
  email?: string | null;
}

export function memberLabel(member: NamedMember | null | undefined): string {
  if (!member) return "";
  return member.full_name || member.email || "";
}
