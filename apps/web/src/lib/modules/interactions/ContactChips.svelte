<script lang="ts">
  /**
   * Who a contactmoment was with (#300): chips plus a type-ahead, the house multi-pick shape
   * (`AssigneePicker`, docs/UX.md — pickers are comboboxes, never native multi-selects).
   *
   * A meeting is with the people who were in it, and a call that reached two of them was one
   * call; the single picker this replaces forced the logger to pick a winner or log it twice.
   *
   * Nothing posts per chip — an edit surface has one save button — so the whole roster goes in
   * one hidden field as a comma-separated list of ids, in chip order. The **first** chip is the
   * lead: it is what the API mirrors onto `contact_id`, what the Contactpersoon column sorts by
   * and what a collapsed row prints, so clicking another chip promotes it, exactly as the
   * assignee picker promotes a primary. Colour alone marks it (docs/UX.md), never a glyph.
   */
  import { X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import type { ContactRoster } from "./roster.svelte";

  let {
    roster,
    name = "contact_ids",
    id = "contact-chips",
    formId,
    oncreate,
  }: {
    roster: ContactRoster;
    name?: string;
    id?: string;
    /** Associate the posted field with a <form id=…> it does not sit inside. */
    formId?: string;
    /** Inline-create (docs/UX.md): the host owns the dialog, so it takes what was typed.
     *  Absent → the picker offers no ＋. */
    oncreate?: (query: string) => void;
  } = $props();

  let comboValue = $state("");

  function pick(contactId: string) {
    roster.add(contactId);
    comboValue = "";
  }
</script>

<input type="hidden" {name} value={roster.picked.join(",")} form={formId} />

<div class="space-y-2">
  {#if roster.picked.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each roster.picked as contactId, index (contactId)}
        <li
          class="relative inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            transition-colors
            {index === 0
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text hover:bg-brand/10 hover:text-brand hover:ring-1 hover:ring-inset hover:ring-brand/30'}"
        >
          {#if index > 0}
            <!-- Stretched over the pill rather than wrapping it: the ✕ is a button too, and
                 buttons cannot nest. -->
            <button
              type="button"
              class="absolute inset-0 cursor-pointer rounded-full"
              title={t("interactions.contacts.make_lead")}
              aria-label={t("interactions.contacts.make_lead")}
              onclick={() => roster.lead(contactId)}
            ></button>
          {/if}
          <span class="pointer-events-none font-medium">
            {roster.label(contactId)}
            {#if index === 0}
              <!-- Colour alone can't carry meaning for a screen reader (WCAG 1.4.1). -->
              <span class="sr-only">({t("interactions.contacts.lead")})</span>
            {/if}
          </span>
          <button
            type="button"
            class="relative rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
            title={t("interactions.contacts.remove")}
            aria-label={t("interactions.contacts.remove")}
            onclick={() => roster.remove(contactId)}><X size={14} /></button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={roster.candidates}
    name="_contact_pick"
    bind:value={comboValue}
    {id}
    placeholder={roster.picked.length
      ? t("interactions.contacts.add_more")
      : t("interactions.field.contact_placeholder")}
    allowEmpty={false}
    onselect={pick}
    keepOpenOnSelect
    {oncreate}
  />

  {#if roster.cleared}
    <!-- Changing the client narrowed the roster past someone who was picked. Dropping them
         silently is what the cascade does to a task; saying so is what stops it reading as
         the form losing the answer on its own. -->
    <p class="text-xs text-text-muted">{t("interactions.field.contact_recheck")}</p>
  {/if}
</div>
