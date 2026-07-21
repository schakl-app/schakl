<script lang="ts">
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import { DOMAIN_COLUMNS } from "$lib/modules/domains/columns";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";

  let { data, form } = $props();

  type Domain = (typeof data.domains)[number];

  // Quick-create from a client page (?new=1&company=): the dialog opens with the client set.
  let showCreate = $state(page.url.searchParams.has("new"));
  const initialCompanyId = page.url.searchParams.get("company") ?? "";
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "domains.domain.write"));
  const canDelete = $derived(can(page.data.user, "domains.domain.delete"));

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

  // The tenant's custom fields join the built-ins as selectable columns with no code here (#24).
  // Layout resolution and persistence are the shared table layout's job.
  const allColumns = $derived([
    ...DOMAIN_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);

  const table = createTableLayout<Domain>({
    all: () => allColumns,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      company: companyCell,
      status: statusCell,
      registrar: registrarCell,
      dns: dnsCell,
      dnssec: dnssecCell,
      email_enabled: emailCell,
      created_at: createdCell,
    }),
  });
</script>

{#snippet nameCell(domain: Domain)}
  <a href="/domains/{domain.id}" class="font-medium text-text hover:text-brand">{domain.name}</a>
{/snippet}

{#snippet companyCell(domain: Domain)}
  <span class="text-text-muted">{domain.company_name}</span>
{/snippet}

{#snippet statusCell(domain: Domain)}
  <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted">
    {t(`domains.status.${domain.status}`)}
  </span>
{/snippet}

{#snippet registrarCell(domain: Domain)}
  <span class="text-text-muted">{domain.registrar_provider_name ?? "—"}</span>
{/snippet}

{#snippet dnsCell(domain: Domain)}
  <span class="text-text-muted">{domain.dns_provider_name ?? "—"}</span>
{/snippet}

{#snippet dnssecCell(domain: Domain)}
  <!-- Three states, like the detail page: never checked ≠ off (#92). -->
  <span class="text-text-muted">
    {domain.dnssec == null
      ? t("domains.dns.unknown")
      : domain.dnssec
        ? t("common.yes")
        : t("common.no")}
  </span>
{/snippet}

{#snippet emailCell(domain: Domain)}
  <span class="text-text-muted">{domain.email_enabled ? t("common.yes") : t("common.no")}</span>
{/snippet}

{#snippet createdCell(domain: Domain)}
  <span class="text-text-muted">{fmtNumericDate(domain.created_at.slice(0, 10))}</span>
{/snippet}

{#snippet rowActions(domain: Domain)}
  <ActionsMenu
    items={[
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => requestDelete(domain.id),
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(domain: Domain)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/domains/{domain.id}" class="min-w-0 flex-1">
      <span class="font-medium text-text">{domain.name}</span>
      <span class="mt-0.5 block truncate text-sm text-text-muted">{domain.company_name}</span>
    </a>
    <span class="shrink-0 rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted">
      {t(`domains.status.${domain.status}`)}
    </span>
    {#if canDelete}
      {@render rowActions(domain)}
    {/if}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("domains.empty")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("domains", t("domains.title")))}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("domains", t("domains.title"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("domains.count", { count: data.total })}</p>
  </div>
  {#if canWrite}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
      onclick={() => (showCreate = true)}>{t("domains.new")}</button
    >
  {/if}
</div>

<!-- The personal column picker: every sort is reachable from here too (docs/UX.md). -->
<div class="mb-4 flex items-center justify-end">
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

<DataTable
  rows={data.domains}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(domain) => `/domains/${domain.id}`}
  actions={canDelete ? rowActions : undefined}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

{#if canWrite}
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
        {initialCompanyId}
        oncreatecompany={quickCreateCompany}
        oncreatecontact={quickCreateContact}
        oncreateprovider={quickCreateProvider}
        created={form?.inlineCreated ?? null}
      />
      {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
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
{/if}

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
