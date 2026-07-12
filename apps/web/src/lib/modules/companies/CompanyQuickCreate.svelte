<script lang="ts">
  /**
   * The full new-client dialog behind a picker's "＋ … toevoegen" (#115, docs/UX.md): real
   * fields plus the tenant's custom-field definitions, prefilled with what was typed — never a
   * name-only stub. Posts to the caller's `createCompany`-style action
   * (`$lib/core/quickcreate.server.ts`), which reports back via `inlineCreated` / `qcError`.
   */
  import { enhance } from "$app/forms";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";

  let {
    open = $bindable(false),
    name = "",
    definitions = [],
    locale,
    action = "?/createCompany",
    error = null,
  }: {
    open?: boolean;
    /** What was typed in the picker; prefills the name. */
    name?: string;
    definitions?: CustomFieldDefinition[];
    locale: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
  } = $props();
</script>

<Modal bind:open title={t("common.quick_create.company")}>
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
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-company-name" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.name")}</label
          >
          <input
            id="qc-company-name"
            name="name"
            value={name}
            required
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="qc-company-status" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.field.status")}</label
          >
          <select
            id="qc-company-status"
            name="status"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm"
          >
            {#each COMPANY_STATUSES as status (status)}
              <option value={status} selected={status === "active"}
                >{t(`companies.status.${status}`)}</option
              >
            {/each}
          </select>
        </div>
        <div class="sm:col-span-2">
          <label for="qc-company-website" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.website")}</label
          >
          <input
            id="qc-company-website"
            name="website"
            placeholder="https://…"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
      </div>
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
