<script lang="ts">
  /**
   * Pick the contact persons for a client that does not exist yet (create form).
   *
   * Same chips + type-ahead shape as {@link LinkField}, but there is no company id to POST links
   * to, so nothing is submitted per chip: the whole selection — existing contacts *and* full
   * new-contact drafts — is serialised into one hidden field, and the create action attaches
   * them once the company row exists. Exactly one chip is the primary contact (★), defaulting to
   * the first one picked.
   *
   * Typing an unknown name opens the *full* new-contact dialog (real fields plus the tenant's
   * contact custom fields), never a name-only stub (docs/UX.md).
   */
  import { Star, X } from "@lucide/svelte";

  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  interface Contact {
    id: string;
    first_name: string;
    last_name?: string | null;
    email?: string | null;
    job_title?: string | null;
  }
  interface ContactDraft {
    first_name: string;
    last_name: string;
    email: string;
    phone: string;
    job_title: string;
    custom: Record<string, unknown>;
  }
  /** A picked contact person: either an existing one, or a draft to be created on save. */
  interface Chip {
    key: string;
    contact_id?: string;
    draft?: ContactDraft;
    label: string;
    hint?: string;
  }

  let {
    contacts = [],
    definitions = [],
    locale,
    name = "contacts",
    id = "company-contact-draft",
  }: {
    contacts?: Contact[];
    definitions?: CustomFieldDefinition[];
    locale: string;
    name?: string;
    id?: string;
  } = $props();

  let chips = $state<Chip[]>([]);
  let primaryKey = $state("");
  let comboValue = $state("");

  // The primary always exists: an explicit star, else the first pick (which is what the API's
  // auto-promote would do anyway).
  const primary = $derived(
    chips.some((c) => c.key === primaryKey) ? primaryKey : (chips[0]?.key ?? ""),
  );

  const fullName = (c: Contact) => [c.first_name, c.last_name].filter(Boolean).join(" ");
  const chosenIds = $derived(new Set(chips.map((c) => c.contact_id).filter(Boolean)));
  const candidates = $derived(
    contacts
      .filter((c) => !chosenIds.has(c.id))
      .map((c) => ({ value: c.id, label: fullName(c), hint: c.email ?? undefined })),
  );

  const payload = $derived(
    JSON.stringify(
      chips.map((c) => ({
        contact_id: c.contact_id,
        draft: c.draft,
        is_primary: c.key === primary,
      })),
    ),
  );

  function pick(contactId: string) {
    if (!contactId) return;
    const contact = contacts.find((c) => c.id === contactId);
    if (!contact) return;
    chips.push({
      key: contact.id,
      contact_id: contact.id,
      label: fullName(contact),
      hint: contact.job_title ?? contact.email ?? undefined,
    });
    comboValue = "";
  }

  function remove(key: string) {
    chips = chips.filter((c) => c.key !== key);
  }

  // --- quick-create dialog ----------------------------------------------------
  let showCreate = $state(false);
  let draft = $state<ContactDraft>(blankDraft());
  let draftSeq = 0;

  function blankDraft(): ContactDraft {
    return { first_name: "", last_name: "", email: "", phone: "", job_title: "", custom: {} };
  }

  function openCreate(query: string) {
    const parts = query.trim().split(/\s+/);
    draft = { ...blankDraft(), first_name: parts.shift() ?? "", last_name: parts.join(" ") };
    showCreate = true;
  }

  function addDraft() {
    if (!draft.first_name.trim()) return;
    const value = $state.snapshot(draft);
    chips.push({
      key: `draft-${++draftSeq}`,
      draft: value,
      label: [value.first_name, value.last_name].filter(Boolean).join(" "),
      hint: value.job_title || value.email || undefined,
    });
    showCreate = false;
    comboValue = "";
  }

  // The dialog lives inside the client form; Enter must add the draft, never submit that form.
  function onDialogKeydown(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addDraft();
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<input type="hidden" {name} value={payload} />

<div class="space-y-2">
  <span class="block text-sm font-medium text-neutral-700">{t("contacts.panel.title")}</span>

  {#if chips.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each chips as chip (chip.key)}
        <li
          class="inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            {chip.key === primary
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-neutral-100 text-neutral-700'}"
        >
          {#if chip.key === primary}
            <span title={t("contacts.primary")}>★</span>
          {:else}
            <button
              type="button"
              class="text-neutral-400 hover:text-brand"
              title={t("contacts.make_primary")}
              aria-label={t("contacts.make_primary")}
              onclick={() => (primaryKey = chip.key)}><Star size={13} /></button
            >
          {/if}
          <span class="font-medium">{chip.label}</span>
          {#if chip.hint}<span class="text-xs opacity-70">{chip.hint}</span>{/if}
          <button
            type="button"
            class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100"
            title={t("contacts.unlink")}
            aria-label={t("contacts.unlink")}
            onclick={() => remove(chip.key)}><X size={14} /></button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={candidates}
    name="_contact_pick"
    bind:value={comboValue}
    {id}
    placeholder={t("contacts.add_person")}
    allowEmpty={false}
    onselect={pick}
    oncreate={openCreate}
  />
</div>

<Modal bind:open={showCreate} title={t("contacts.new")}>
  <!-- Not a <form>: this dialog sits inside the client form, so it collects a draft instead. -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="space-y-3" role="group" onkeydown={onDialogKeydown}>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="qcd-first" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.first_name")}</label
        >
        <input id="qcd-first" bind:value={draft.first_name} required class={inputClass} />
      </div>
      <div>
        <label for="qcd-last" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.last_name")}</label
        >
        <input id="qcd-last" bind:value={draft.last_name} class={inputClass} />
      </div>
      <div>
        <label for="qcd-email" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.email")}</label
        >
        <input id="qcd-email" type="email" bind:value={draft.email} class={inputClass} />
      </div>
      <div>
        <label for="qcd-phone" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.phone")}</label
        >
        <input id="qcd-phone" bind:value={draft.phone} class={inputClass} />
      </div>
      <div class="sm:col-span-2">
        <label for="qcd-job" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.job_title")}</label
        >
        <input id="qcd-job" bind:value={draft.job_title} class={inputClass} />
      </div>
    </div>

    {#if definitions.length > 0}
      <!-- The dialog is destroyed on close, so this remounts (and re-seeds) per draft. -->
      <CustomFieldsForm
        {definitions}
        {locale}
        name={null}
        onchange={(values) => (draft.custom = values)}
      />
    {/if}

    <div class="flex justify-end gap-2 pt-1">
      <button
        type="button"
        class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
        onclick={() => (showCreate = false)}>{t("common.cancel")}</button
      >
      <button
        type="button"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        disabled={!draft.first_name.trim()}
        onclick={addDraft}>{t("common.add")}</button
      >
    </div>
  </div>
</Modal>
