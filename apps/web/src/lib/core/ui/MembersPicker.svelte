<script lang="ts">
  /**
   * Name several colleagues. `MemberPicker`'s plural: the same roster, the same lifecycle rule
   * (a deactivated colleague is out of the opening list, findable by typing, labelled when
   * found), drawn as the chips + type-ahead shape `AssigneePicker` established — minus the ★,
   * because here nobody is *the* one. "Schedule this block for the three of us" is a list of
   * equals, and a marker that means "primary" on the assignee chips and nothing on these would
   * teach the reader it means nothing.
   *
   * Every chip posts as its own hidden field under `name`, so the form action reads
   * `formData.getAll(name)` and no JSON has to be agreed on between the two: a list of ids is
   * what a form already knows how to say. An empty list posts nothing at all — "nobody" is the
   * host's to refuse, since it is the host that knows whether nobody is an answer.
   *
   * No `oncreate`, for `MemberPicker`'s reason: an employee is invited, never created from a
   * dropdown (docs/UX.md).
   */
  import { X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import {
    memberArchivedLabel,
    memberLabel,
    splitMemberOptions,
    type PickerMember,
  } from "$lib/core/members";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  let {
    members = [],
    value = $bindable([]),
    name,
    id = name,
    formId,
    placeholder = "",
    onchange,
  }: {
    /** The roster, as `/members/lookup` answers it. `is_active` decides the bucket. */
    members?: readonly PickerMember[];
    /** The picked user ids, in the order they were named. */
    value?: string[];
    /** The name every chip's hidden field posts under. */
    name: string;
    id?: string;
    /** Associate the posted values with an external `<form id=…>` (single-save layouts). */
    formId?: string;
    placeholder?: string;
    onchange?: (value: string[]) => void;
  } = $props();

  let comboValue = $state("");

  const label = (userId: string) => {
    const member = members.find((m) => m.user_id === userId);
    return member ? memberLabel(member) : userId;
  };

  // What the field holds is drawn as a chip, so a picked member is excluded from the options
  // rather than filtered out of the roster — and a deactivated colleague the chips already name
  // stays a chip, while one they do not name is reachable by typing, under "Gedeactiveerd".
  const options = $derived(splitMemberOptions(members, { exclude: value }));

  function pick(userId: string) {
    if (!userId || value.includes(userId)) return;
    value = [...value, userId];
    comboValue = "";
    onchange?.(value);
  }

  function remove(userId: string) {
    value = value.filter((u) => u !== userId);
    onchange?.(value);
  }
</script>

{#each value as userId (userId)}
  <input type="hidden" {name} value={userId} form={formId} />
{/each}

<div class="space-y-2">
  {#if value.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each value as userId (userId)}
        <li
          class="inline-flex items-center gap-1.5 rounded-full bg-brand/10 py-1 pl-2.5 pr-1.5 text-sm text-brand ring-1 ring-inset ring-brand/30"
        >
          <span class="font-medium">{label(userId)}</span>
          <button
            type="button"
            class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
            title={t("assignees.remove")}
            aria-label="{t('assignees.remove')}: {label(userId)}"
            onclick={() => remove(userId)}><X size={14} /></button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={options.live}
    name="_{name}_pick"
    bind:value={comboValue}
    {id}
    {placeholder}
    allowEmpty={false}
    onselect={pick}
    keepOpenOnSelect
    archived={options.retired}
    archivedLabel={memberArchivedLabel()}
  />
</div>
