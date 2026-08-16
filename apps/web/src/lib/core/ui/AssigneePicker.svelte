<script lang="ts">
  /**
   * Pick the employees working a client, a project or a task: one is the verantwoordelijke, the
   * rest are assigned. Same chips + type-ahead shape as the contact picker (docs/UX.md: pickers
   * are comboboxes, never native multi-selects).
   *
   * The primary is marked by a ★ **and** the brand colour, matching `LinkField`. Colour alone was
   * the original rule and was reversed there for a reason that applies identically here: on a
   * gold-branded tenant a brand-coloured pill is indistinguishable from an amber warning chip, and
   * a colour reaches no screen reader at all (WCAG 1.4.1). The two *pill* surfaces have to agree —
   * they sit on the same screens, and a marker that means "primary" on one card and nothing on the
   * next teaches the reader that it means nothing. (`Assignees`, the read-only row, is not a pill
   * at all: it draws avatars and separates the primary by full-vs-muted contrast, which is neither
   * the failing signal nor improved by a glyph beside a face.)
   *
   * Clicking any other chip promotes it, so the marker never doubles as a control.
   *
   * Nothing is posted per chip — an edit surface has exactly one save button (docs/UX.md), so the
   * whole roster is serialised into one hidden field that the form action forwards to the API as
   * `assignees`. `formId` associates it with a <form> it does not sit inside.
   */
  import { Star, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  interface Member {
    user_id: string;
    full_name?: string | null;
    email: string | null;
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

  // Someone is always primary: an explicit pick, else the first chip — which is what the API would
  // do with a roster that designates nobody anyway.
  const primary = $derived(picked.includes(primaryId) ? primaryId : (picked[0] ?? ""));

  const label = (userId: string) => {
    const member = members.find((m) => m.user_id === userId);
    return member ? memberLabel(member) : userId;
  };
  const candidates = $derived(
    members
      .filter((m) => !picked.includes(m.user_id))
      .map((m) => ({ value: m.user_id, label: memberLabel(m) })),
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
          class="relative inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            transition-colors
            {userId === primary
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text hover:bg-brand/10 hover:text-brand hover:ring-1 hover:ring-inset hover:ring-brand/30'}"
          title={userId === primary
            ? `${label(userId)} · ${t("assignees.primary")}`
            : t("assignees.make_primary")}
        >
          {#if userId !== primary}
            <!-- The whole chip promotes, so the label stays plain text and the hover previews the
                 colour it is about to take. Stretched over the pill rather than wrapping it: the ✕
                 is a button too, and buttons cannot nest. -->
            <button
              type="button"
              class="absolute inset-0 cursor-pointer rounded-full"
              title={t("assignees.make_primary")}
              aria-label={t("assignees.make_primary")}
              onclick={() => (primaryId = userId)}
            ></button>
          {/if}
          <span class="pointer-events-none flex items-center gap-1 font-medium">
            {#if userId === primary}
              <!-- A glyph, not only a colour — and the label beside it, since neither colour nor
                   shape reaches a screen reader (WCAG 1.4.1). -->
              <Star size={12} class="shrink-0 fill-current" aria-hidden="true" />
              <span class="sr-only">({t("assignees.primary")})</span>
            {/if}
            {label(userId)}
          </span>
          <button
            type="button"
            class="relative rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
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
    keepOpenOnSelect
  />
</div>
