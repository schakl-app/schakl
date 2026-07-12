<script lang="ts">
  /**
   * The full new-hosting dialog behind the website form's hosting picker (#115, docs/UX.md):
   * the real `HostingForm` field set (incl. the tenant's hosting custom fields), name prefilled
   * with what was typed. Posts to the caller's `createHosting`-style action, which answers with
   * `inlineCreated: { slot: "hosting_account", id }` / `qcError`.
   */
  import { enhance } from "$app/forms";
  import type { components } from "$lib/core/api/schema";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";
  import HostingForm from "$lib/modules/hosting/HostingForm.svelte";

  type Provider = components["schemas"]["ProviderRead"];
  type Definition = components["schemas"]["CustomFieldDefinitionRead"];
  type Member = components["schemas"]["MemberLookup"];

  let {
    open = $bindable(false),
    name = "",
    companies,
    providers,
    employees,
    contacts,
    agencyLabel,
    definitions,
    locale,
    action = "?/createHosting",
    error = null,
  }: {
    open?: boolean;
    /** What was typed in the picker; prefills the name. */
    name?: string;
    companies: { id: string; name: string }[];
    providers: Provider[];
    employees: Member[];
    contacts: { id: string; name: string }[];
    agencyLabel: string;
    definitions: Definition[];
    locale: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
  } = $props();
</script>

<Modal bind:open title={t("hosting.new")}>
  {#key name + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") open = false;
          void update({ reset: false });
        }}
    >
      <HostingForm
        nameDefault={name}
        {companies}
        {providers}
        {employees}
        {contacts}
        {agencyLabel}
        {definitions}
        {locale}
        idPrefix="qc-hosting"
      />
      {#if error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="mt-4 flex justify-end gap-2">
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
