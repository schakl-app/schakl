<script lang="ts">
  /**
   * The full new-contact dialog behind a picker's "＋ … toevoegen" (#115, docs/UX.md): real
   * fields plus the tenant's contact custom-field definitions, prefilled with what was typed —
   * never a name-only stub. Unlike `ContactDraftField` (which collects drafts inside a client
   * form that hasn't been saved yet) this creates the contact immediately: it posts to the
   * caller's `createContact`-style action (`$lib/core/quickcreate.server.ts`), which reports
   * back via `inlineCreated` / `qcError` so the asking picker auto-selects the new contact.
   */
  import { enhance } from "$app/forms";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    name = "",
    email = "",
    linkCompany = null,
    definitions = [],
    locale,
    action = "?/createContact",
    error = null,
    pickerSlot = "contact",
  }: {
    open?: boolean;
    /** What was typed in the picker; split into first/last name. */
    name?: string;
    /** Prefilled address — a participant chip opens this dialog with the email known (#160). */
    email?: string;
    /** Offer linking the new contact to this client (checked by default), e.g. the client an
     *  email contact moment hangs on (#160). The action decides what to do with `company_id`. */
    linkCompany?: { id: string; name: string } | null;
    definitions?: CustomFieldDefinition[];
    locale: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects (PartyPicker). */
    pickerSlot?: string;
  } = $props();

  // "ada lovelace" → first name "ada", last name "lovelace" (same split as ContactDraftField).
  const parts = $derived(name.trim().split(/\s+/).filter(Boolean));
  const firstName = $derived(parts[0] ?? "");
  const lastName = $derived(parts.slice(1).join(" "));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<Modal bind:open title={t("common.quick_create.contact")}>
  {#key name + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") open = false;
          void update({ reset: false });
        }}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={pickerSlot} />
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-contact-first" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.first_name")}</label
          >
          <input
            id="qc-contact-first"
            name="first_name"
            value={firstName}
            required
            class={inputClass}
          />
        </div>
        <div>
          <label for="qc-contact-last" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.last_name")}</label
          >
          <input id="qc-contact-last" name="last_name" value={lastName} class={inputClass} />
        </div>
        <div>
          <label for="qc-contact-email" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.email")}</label
          >
          <input id="qc-contact-email" name="email" type="email" value={email} class={inputClass} />
        </div>
        <div>
          <label for="qc-contact-phone" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.phone")}</label
          >
          <input id="qc-contact-phone" name="phone" class={inputClass} />
        </div>
        <div class="sm:col-span-2">
          <label for="qc-contact-job" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.job_title")}</label
          >
          <input id="qc-contact-job" name="job_title" class={inputClass} />
        </div>
      </div>
      {#if linkCompany}
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="company_id" value={linkCompany.id} checked />
          {t("contacts.link_to_company", { name: linkCompany.name })}
        </label>
      {/if}
      {#if definitions.length > 0}
        <CustomFieldsForm {definitions} {locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("common.create")}</button
        >
      </div>
    </form>
  {/key}
</Modal>
