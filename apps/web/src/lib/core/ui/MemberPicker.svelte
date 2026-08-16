<script lang="ts">
  /**
   * Name one colleague. The single control every screen uses when the answer is a person on the
   * team — a filter's "whose", a rule's assignee, a template's owner, a contactmoment's mailbox.
   *
   * It exists because the rule about *which* colleagues a picker opens with had been written down
   * once (`$lib/core/members`) and then re-applied by hand at every call site: eight screens each
   * spelled out `splitMemberOptions(...)`, `archived={split.retired}` and `archivedLabel={…}`, and
   * the three that predated the helper were still native `<select>`s that could not hide anything
   * behind a search at all — so a colleague who left in March sat between two people who are still
   * here, spelled identically, as an ordinary suggestion. Work assigned there lands in an account
   * that cannot sign in to see it, and nothing on any screen says why it is never picked up.
   *
   * Folding it into one component is what makes that rule a property of the app rather than of
   * whoever wrote the screen last: a deactivated colleague is out of the opening list, findable by
   * typing, labelled "Gedeactiveerd" when found, and always offered while the field already holds
   * them — everywhere, including the next screen nobody has written yet.
   *
   * Two things are deliberately *not* here. There is no `oncreate`: an employee is invited, never
   * created from a dropdown (docs/UX.md), so this is the one entity-reference picker with no ＋.
   * And the component holds no vocabulary of its own — the heading and the status words come from
   * `$lib/core/members`, which is where the members' lifecycle is defined.
   */
  import { memberArchivedLabel, splitMemberOptions, type PickerMember } from "$lib/core/members";
  import type { PickerOption } from "$lib/core/picker";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  let {
    members = [],
    value = $bindable(""),
    name,
    id = name,
    formId,
    placeholder = "",
    ariaLabel,
    allowEmpty = true,
    extra = [],
    exclude = [],
    onselect,
  }: {
    /** The roster, as `/members/lookup` answers it. `is_active` decides the bucket. */
    members?: readonly PickerMember[];
    /** The picked user id — or one of `extra`'s values, which are not people. */
    value?: string;
    /**
     * The hidden input's name. A picker whose host serialises its own state (a rule's action
     * list, a template's items) passes a `_`-prefixed one it does not read, exactly as the
     * filter bars do: the control still needs a field to post through, and nothing should be
     * tempted to read it.
     */
    name: string;
    id?: string;
    /** Associate the posted value with an external `<form id=…>` (single-save layouts). */
    formId?: string;
    placeholder?: string;
    /** Accessible name where the control has no visible `<label>` — a toolbar filter. */
    ariaLabel?: string;
    allowEmpty?: boolean;
    /**
     * Choices that lead the list and are not a person: "mijn" / "iedereen" on a filter, "de
     * verantwoordelijke" on a template that resolves the name at apply time. They are never
     * split, never retired, and always on offer — they answer the question the picker asks
     * without naming anybody.
     */
    extra?: readonly PickerOption[];
    /**
     * User ids never offered. A filter that already spells "mijn" excludes the signed-in user,
     * or the same person is two rows with different words for the same answer.
     */
    exclude?: readonly string[];
    onselect?: (value: string) => void;
  } = $props();

  // `selectedId: value` is what keeps a deactivated colleague the field already holds on offer —
  // a value that resolves to nothing reads as an empty box, not as "this account is closed".
  const split = $derived(splitMemberOptions(members, { selectedId: value, exclude }));
  const items = $derived([...extra, ...split.live]);
</script>

<Combobox
  {items}
  {name}
  {id}
  {formId}
  {placeholder}
  {ariaLabel}
  {allowEmpty}
  {onselect}
  bind:value
  archived={split.retired}
  archivedLabel={memberArchivedLabel()}
/>
