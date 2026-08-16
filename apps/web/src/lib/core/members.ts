import { t } from "$lib/core/i18n";
import { splitLifecycle, type LifecycleSplit, type StatusedOption } from "$lib/core/picker";

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

/**
 * Which colleagues a picker opens with, and which are only findable by typing their name.
 *
 * A deactivated account is the employee equivalent of an archived client or a finished project,
 * and it had none of the treatment those two already get: `/members/lookup` answers the whole
 * roster and every picker in the app rendered it flat, so somebody who left in March sat between
 * two colleagues, spelled identically, as an ordinary suggestion. Work then gets assigned to an
 * account that cannot sign in to see it, and nothing anywhere says why it is never picked up.
 *
 * Removing them outright is the other mistake, and the more common one. Their name still has to
 * render on the task they were holding when they left, an approver has to be able to file last
 * quarter's hours under them, and a manager filtering the timesheet is asking about exactly the
 * person who is gone. So this is `splitLifecycle` with the members' own two-word vocabulary:
 * out of the opening list, under the "Gedeactiveerd" heading once the user types, never above
 * a colleague who is still here, and always offered when the field already holds them.
 *
 * `status` is derived rather than sent, because an account has one bit and not a vocabulary
 * (`is_active`). Deriving it here is what lets the shared splitter — and every screen — speak
 * about members in the same words it uses for clients and projects.
 */

/** The shape `/members/lookup` returns; extra fields are ignored. */
export interface PickerMember extends NamedMember {
  user_id: string;
  /** Absent is treated as active — an older payload must not retire the whole roster. */
  is_active?: boolean;
}

/** One bit, two words — the whole of the members' lifecycle vocabulary. */
const RETIRED = ["inactive"] as const;
const QUIET = ["active"] as const;

function memberStatus(member: PickerMember): string {
  return member.is_active === false ? "inactive" : "active";
}

export interface MemberPickerOptions {
  /** The member(s) the field(s) already hold — always offered, deactivated or not. */
  selectedId?: string | readonly (string | null | undefined)[];
  /**
   * Drop these user ids entirely: a multi-picker draws what it holds as chips, so a chosen
   * member belongs in neither bucket, and `selectedId` would put them back on offer.
   */
  exclude?: readonly string[];
}

export function splitMemberOptions(
  members: readonly PickerMember[],
  { selectedId = [], exclude = [] }: MemberPickerOptions = {},
): LifecycleSplit {
  const skip = new Set(exclude);
  const options: StatusedOption[] = members
    .filter((member) => !skip.has(member.user_id))
    .map((member) => ({
      value: member.user_id,
      label: memberLabel(member),
      status: memberStatus(member),
    }));
  return splitLifecycle(options, {
    retired: RETIRED,
    quiet: QUIET,
    statusLabel: (status: string) => t(`members.status.${status}`),
    selectedId,
  });
}

/** The heading `Combobox` draws above the search-only rows. */
export function memberArchivedLabel(): string {
  return t("members.picker.archived");
}

/**
 * There is no `partitionMembers` anymore, and the deletion is worth a note.
 *
 * It existed for the three controls that were still native `<select>`s — the interactions owner
 * filter, the automation rule's assignee, the task template's — where the rule above had to
 * degrade to "last in the list, under an `<optgroup>` that says what they are", because a
 * `<select>` has no search to hide anything behind. Those three are `MemberPicker`
 * (`$lib/core/ui/MemberPicker.svelte`) now, which is the conversion docs/UX.md asked for at
 * #256, so the degraded shape has no callers — and leaving it exported is how a fourth screen
 * would come to have one.
 *
 * Name a colleague with `MemberPicker`. Reach for `splitMemberOptions` directly only where the
 * control is not a single-value picker: the assignee chips, the party picker.
 */
