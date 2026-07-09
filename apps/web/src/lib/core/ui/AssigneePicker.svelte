<script lang="ts">
  /**
   * Pick the employees working a client or project: one is the verantwoordelijke (★), the rest
   * are assigned. Same chips + type-ahead shape as the contact picker (docs/UX.md: pickers are
   * comboboxes, never native multi-selects).
   *
   * Nothing is posted per chip — an edit surface has exactly one save button (docs/UX.md), so the
   * whole roster is serialised into one hidden field that the form action forwards to the API as
   * `assignees`. `formId` associates it with a <form> it does not sit inside.
   */
  import { Star, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string;
  }
  interface Assignee {
    user_id: string;
    is_primary: boolean;
  }

  let {
    members = [],
    value = [],
    name = "assignees",
    id = "assignee-picker",
    formId,
    placeholder,
  }: {
    members?: Member[];
    /** The saved roster; primary first, as the API returns it. */
    value?: Assignee[];
    name?: string;
    id?: string;
    formId?: string;
    placeholder?: string;
  } = $props();

  let picked = $state<string[]>(value.map((a) => a.user_id));
  let primaryId = $state(value.find((a) => a.is_primary)?.user_id ?? "");
  let comboValue = $state("");

  // The star always lands on someone: an explicit pick, else the first chip — which is what the
  // API would do with an unstarred roster anyway.
  const primary = $derived(picked.includes(primaryId) ? primaryId : (picked[0] ?? ""));

  const label = (userId: string) => {
    const member = members.find((m) => m.user_id === userId);
    return member ? member.full_name || member.email : userId;
  };
  const candidates = $derived(
    members
      .filter((m) => !picked.includes(m.user_id))
      .map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );

  const payload = $derived(
    JSON.stringify(picked.map((userId) => ({ user_id: userId, is_primary: userId === primary }))),
  );

  function pick(userId: string) {
    if (!userId || picked.includes(userId)) return;
    picked = [...picked, userId];
    comboValue = "";
  }

  function remove(userId: string) {
    picked = picked.filter((u) => u !== userId);
  }
</script>

<input type="hidden" {name} value={payload} form={formId} />

<div class="space-y-2">
  {#if picked.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each picked as userId (userId)}
        <li
          class="inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            {userId === primary
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text'}"
        >
          {#if userId === primary}
            <span title={t("assignees.primary")}>★</span>
          {:else}
            <button
              type="button"
              class="text-text-muted hover:text-brand"
              title={t("assignees.make_primary")}
              aria-label={t("assignees.make_primary")}
              onclick={() => (primaryId = userId)}><Star size={13} /></button
            >
          {/if}
          <span class="font-medium">{label(userId)}</span>
          <button
            type="button"
            class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100"
            title={t("assignees.remove")}
            aria-label={t("assignees.remove")}
            onclick={() => remove(userId)}><X size={14} /></button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={candidates}
    name="_assignee_pick"
    bind:value={comboValue}
    {id}
    placeholder={placeholder ?? t("assignees.add")}
    allowEmpty={false}
    onselect={pick}
  />
</div>
