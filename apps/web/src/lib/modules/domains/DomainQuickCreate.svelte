<script lang="ts">
  /**
   * The full new-domain dialog behind the website form's domain picker (#115, docs/UX.md):
   * the real `DomainForm` field set (incl. the tenant's domain custom fields), name prefilled
   * with what was typed. Posts to the caller's `createDomain`-style action, which answers with
   * `inlineCreated: { slot: "domain", id }` / `qcError`.
   *
   * Its own pickers can inline-create too (registrar/DNS/email provider, client, contact): the
   * caller passes those callbacks straight through to `DomainForm`, so those dialogs stack over
   * this one.
   */
  import { enhance } from "$app/forms";
  import type { components } from "$lib/core/api/schema";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";

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
    initialCompanyId = "",
    action = "?/createDomain",
    error = null,
    oncreatecompany,
    oncreatecontact,
    oncreateprovider,
    created = null,
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
    /** Preselects the client when the website dialog was scoped to one. */
    initialCompanyId?: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    oncreatecompany?: (name: string, slot?: string) => void;
    oncreatecontact?: (name: string, slot: string) => void;
    oncreateprovider?: (kind: "registrar" | "dns" | "email", name: string) => void;
    /** The entity a nested quick-create just made; auto-selected inside the form. */
    created?: { slot: string; id: string } | null;
  } = $props();

  const busy = new InFlight();
</script>

<Modal bind:open title={t("domains.new")}>
  {#key name + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") open = false;
        void update({ reset: false });
      })}
    >
      <DomainForm
        {companies}
        {providers}
        {employees}
        {contacts}
        {agencyLabel}
        {definitions}
        {locale}
        idPrefix="qc-domain"
        nameDefault={name}
        {initialCompanyId}
        {oncreatecompany}
        {oncreatecontact}
        {oncreateprovider}
        {created}
      />
      {#if error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>
