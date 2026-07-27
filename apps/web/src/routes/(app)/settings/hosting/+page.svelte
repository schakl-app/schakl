<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import HostingForm from "$lib/modules/hosting/HostingForm.svelte";
  import type { components } from "$lib/core/api/schema";

  type Hosting = components["schemas"]["HostingRead"];

  let { data, form } = $props();

  // Quick-create from a client page (?new=1&company=): the dialog opens with the client set.
  let showModal = $state(page.url.searchParams.has("new"));
  const initialCompanyId = page.url.searchParams.get("company") ?? "";
  let editing = $state<Hosting | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  const busy = new InFlight();

  // Inline-create from the form's pickers (#115): "＋ … toevoegen" opens these over the modal.
  // The slot names the picker that asked, so its `inlineCreated` auto-selects only there.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  let qcProviderOpen = $state(false);
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

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(h: Hosting) {
    editing = h;
    showModal = true;
  }
  function requestDelete(id: string) {
    deleteId = id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("hosting.title"))}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("hosting.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("hosting.count", { count: data.total })}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("hosting.new")}</button
  >
</div>

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.hosting.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("hosting.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.hosting as item (item.id)}
        <li class="flex items-center gap-3 px-4 py-3">
          <span class="flex-1 text-sm font-medium text-text">{item.name}</span>
          {#if item.provider_name}
            <span class="text-xs text-text-muted">{item.provider_name}</span>
          {/if}
          {#if item.ip_address}
            <span class="font-mono text-xs text-text-muted">{item.ip_address}</span>
          {/if}
          {#if item.company_name}
            <span class="text-xs text-text-muted">{item.company_name}</span>
          {/if}
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(item) },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(item.id),
              },
            ]}
          />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal bind:open={showModal} title={editing ? t("hosting.edit") : t("hosting.new")}>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/save"
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") showModal = false;
        void update({ reset: false });
      })}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <HostingForm
        hosting={editing}
        companies={data.companies}
        providers={data.providers}
        employees={data.employees}
        contacts={data.contacts}
        agencyLabel={data.agencyLabel}
        definitions={data.definitions}
        locale={data.locale}
        idPrefix={editing ? `edit-${editing.id}` : "new-hosting"}
        initialCompanyId={editing ? "" : initialCompanyId}
        oncreatecompany={quickCreateCompany}
        oncreatecontact={quickCreateContact}
        oncreateprovider={(_kind, name) => {
          qcProviderName = name;
          qcProviderOpen = true;
        }}
        created={form?.inlineCreated ?? null}
      />
      {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/key}
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
  kind="hosting"
  name={qcProviderName}
  error={form?.qcError ?? null}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("hosting.delete")}
  message={t("hosting.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
