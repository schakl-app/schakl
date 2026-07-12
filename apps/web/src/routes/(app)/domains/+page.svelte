<script lang="ts">
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Inline-create from the form's pickers (#115): "＋ … toevoegen" opens these over the modal.
  // The slot names the picker that asked, so its `inlineCreated` auto-selects only there.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  let qcProviderOpen = $state(false);
  let qcProviderKind = $state<"registrar" | "dns" | "email">("registrar");
  let qcProviderName = $state("");

  function quickCreateCompany(name: string, slot = "company") {
    qcCompanyName = name;
    qcCompanySlot = slot;
    qcCompanyOpen = true;
  }
  function quickCreateContact(name: string, slot: string) {
    qcContactName = name;
    qcContactSlot = slot;
    qcContactOpen = true;
  }
  function quickCreateProvider(kind: "registrar" | "dns" | "email", name: string) {
    qcProviderKind = kind;
    qcProviderName = name;
    qcProviderOpen = true;
  }

  function requestDelete(id: string) {
    deleteId = id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{t("domains.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("domains.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("domains.count", { count: data.total })}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
    onclick={() => (showCreate = true)}>{t("domains.new")}</button
  >
</div>

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.domains.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("domains.empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="border-b border-border text-left text-xs uppercase text-text-muted">
          <tr>
            <th class="px-4 py-2 font-medium">{t("domains.name")}</th>
            <th class="px-4 py-2 font-medium">{t("domains.company")}</th>
            <th class="px-4 py-2 font-medium">{t("domains.status")}</th>
            <th class="px-4 py-2 font-medium">{t("domains.registrar")}</th>
            <th class="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each data.domains as domain (domain.id)}
            <tr class="hover:bg-surface">
              <td class="px-4 py-2">
                <a href="/domains/{domain.id}" class="font-medium text-brand hover:underline"
                  >{domain.name}</a
                >
              </td>
              <td class="px-4 py-2 text-text-muted">{domain.company_name}</td>
              <td class="px-4 py-2">
                <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                  >{t(`domains.status.${domain.status}`)}</span
                >
              </td>
              <td class="px-4 py-2 text-text-muted">{domain.registrar_provider_name ?? "—"}</td>
              <td class="px-4 py-2 text-right">
                <ActionsMenu
                  compact
                  items={[
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => requestDelete(domain.id),
                    },
                  ]}
                />
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<Modal bind:open={showCreate} title={t("domains.new")}>
  <form
    method="POST"
    action="?/create"
    use:enhance={() =>
      ({ result, update }) => {
        if (result.type === "success") showCreate = false;
        void update({ reset: false });
      }}
  >
    <DomainForm
      companies={data.companies}
      providers={data.providers}
      employees={data.employees}
      contacts={data.contacts}
      agencyLabel={data.agencyLabel}
      definitions={data.definitions}
      locale={data.locale}
      idPrefix="new-domain"
      oncreatecompany={quickCreateCompany}
      oncreatecontact={quickCreateContact}
      oncreateprovider={quickCreateProvider}
      created={form?.inlineCreated ?? null}
    />
    {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (showCreate = false)}>{t("common.cancel")}</button
      >
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
        >{t("common.save")}</button
      >
    </div>
  </form>
</Modal>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  pickerSlot={qcCompanySlot}
  definitions={data.companyDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>
<ContactQuickCreate
  bind:open={qcContactOpen}
  name={qcContactName}
  pickerSlot={qcContactSlot}
  definitions={data.contactDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>
<ProviderQuickCreate
  bind:open={qcProviderOpen}
  kind={qcProviderKind}
  name={qcProviderName}
  error={form?.qcError ?? null}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("domains.delete")}
  message={t("domains.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
