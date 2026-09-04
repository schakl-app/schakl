<script lang="ts">
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { components } from "$lib/core/api/schema";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { columnsForViewer, customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import { DOMAIN_COLUMNS } from "$lib/modules/domains/columns";
  import type { DomainFilterKey } from "$lib/modules/domains/filters";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";

  let { data, form } = $props();

  type Domain = (typeof data.domains)[number];

  // Deep link from a client card: `?company=` filters the list *and* prefills the create dialog,
  // and `?new=1` opens it. One parameter for both, because they are the same intent — "I am
  // working on this client's domains" — and two would let the list and the dialog disagree.
  let showCreate = $state(page.url.searchParams.has("new"));
  const initialCompanyId = $derived(data.filters.company ?? "");
  // One split of the client lookup, used by the filter bar, the bulk dialog and the create
  // form alike: an archived client is never suggested and always findable, and the one the
  // list is filtered by stays on offer whatever became of it.
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: initialCompanyId }),
  );
  let deleteId = $state("");
  let confirmDelete = $state(false);
  const busy = new InFlight();

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

  // --- bulk (the ✎ selection mode in the toolbar) --------------------------------------
  // The register is where one shared value over a long list is the normal case: a portfolio
  // moves to another registrar, a client's names are pointed at a new DNS or mail provider, a
  // batch changes hands, or somebody finally decides which of them we invoice. The name is
  // deliberately absent — it *is* the record. Mirrors `apps/api/app/modules/domains/bulk.py`;
  // labels are the import's, so the two surfaces that name the same column never differ.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);

  // Typed off the generated client so a status the API dropped stops compiling here.
  const DOMAIN_STATUSES: components["schemas"]["DomainStatus-Input"][] = [
    "active",
    "redirect",
    "parked",
    "expired",
    "inactive",
  ];

  // The pickers reuse the lists the section layout already loaded for the create form, so the
  // dialog costs no request of its own (docs/PERFORMANCE.md). Providers are filtered per slot
  // by kind, exactly as `DomainForm` does — a registrar is never offered as a mail provider.
  const providerOptions = (kind: string) =>
    data.providers.filter((p) => p.kind === kind).map((p) => ({ value: p.id, label: p.name }));

  // Derived, not a const: a quick-create from the create modal refreshes `companies` and
  // `providers` mid-life, and the newly made row has to be pickable here too.
  const bulkFields: BulkFieldDef[] = $derived([
    {
      key: "status",
      label: t("impex.column.domain.status"),
      type: "select",
      options: DOMAIN_STATUSES.map((status) => ({
        value: status,
        label: t(`domains.status.${status}`),
      })),
    },
    {
      key: "company",
      label: t("impex.column.domain.company"),
      type: "fk",
      options: companyPicker.live,
    },
    {
      key: "registrar_provider",
      label: t("impex.column.domain.registrar_provider"),
      type: "fk",
      options: providerOptions("registrar"),
    },
    {
      key: "dns_provider",
      label: t("impex.column.domain.dns_provider"),
      type: "fk",
      options: providerOptions("dns"),
    },
    {
      key: "email_provider",
      label: t("impex.column.domain.email_provider"),
      type: "fk",
      options: providerOptions("email"),
    },
    {
      key: "invoiceable",
      label: t("impex.column.domain.invoiceable"),
      type: "bool",
      // Clearable (#298): emptying it is not "do not invoice", it hands the decision back to
      // the register — so the tick says that instead of reading as a blank.
      clearable: true,
      clearLabel: t("domains.bulk.invoiceable_auto"),
    },
    {
      key: "next_invoice_date",
      label: t("impex.column.domain.next_invoice_date"),
      type: "date",
      // Clearable here and not in the import, on purpose: over a selection somebody ticked row
      // by row, "put these back on the date they should have" is the repair this control is
      // for, while in a file the same blank is just a column nobody filled in
      // (`app/api/app/modules/domains/bulk.py` argues it in full).
      clearable: true,
      clearLabel: t("domains.bulk.renewal_reset"),
    },
  ]);
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "domains.domain.write",
    deletePermission: "domains.domain.delete",
    deleteMessage: t("domains.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
  });

  // --- the filter bar (core/filters) ----------------------------------------------------
  // Six questions an agency asks of its register. Every option list here is one the section
  // layout already loaded for the create form, so the bar costs no request of its own; the
  // providers are split by kind exactly as `DomainForm` splits them.
  //
  // The two-value ones are `select`, not a pair of pills, and their labels say the whole thing:
  // a Combobox shows the *selected* label with the placeholder gone, so "Ja" would leave the
  // screen reading "Ja" with nothing to say Ja to.
  const filtering = $derived(Object.keys(data.filters).length > 0);
  const filterDefs: FilterDef<DomainFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("domains.search_placeholder") },
    {
      // Every placeholder here is the *column's* own label key, so the filter and the column it
      // narrows can never end up calling the same thing two different names.
      kind: "select",
      key: "company",
      placeholder: t("domains.company"),
      // Archived clients behind the search rather than among the live ones; the client this
      // list is currently filtered by is always offered (`companies/picker.ts`).
      options: companyPicker.live,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "pills",
      key: "status",
      options: DOMAIN_STATUSES.map((status) => ({
        value: status,
        label: t(`domains.status.${status}`),
      })),
    },
    // A filter on a column the viewer's table does not have (core/table/columns.ts, audience).
    ...(page.data.user?.isPortal
      ? []
      : [
          {
            kind: "select" as const,
            key: "registrar" as const,
            placeholder: t("domains.registrar"),
            options: providerOptions("registrar"),
          },
        ]),
    {
      kind: "select",
      key: "dns",
      placeholder: t("domains.dns"),
      options: providerOptions("dns"),
    },
    // Whether *we* bill the renewal is the agency's decision — the column is staff-only, and
    // so is a filter on it.
    ...(page.data.user?.isPortal
      ? []
      : [
          {
            kind: "select" as const,
            key: "invoiceable" as const,
            placeholder: t("domains.invoiceable.legend"),
            options: [
              { value: "true", label: t("domains.filter.invoiceable_yes") },
              { value: "false", label: t("domains.filter.invoiceable_no") },
            ],
          },
        ]),
  ]);

  // The tenant's custom fields join the built-ins as selectable columns with no code here (#24).
  // Layout resolution and persistence are the shared table layout's job.
  const allColumns = $derived([
    ...columnsForViewer(DOMAIN_COLUMNS, page.data.user),
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
      next_invoice: renewalCell,
      register_expires: registerExpiryCell,
      price: priceCell,
      invoiceable: invoiceableCell,
      created_at: createdCell,
    }),
  });
</script>

{#snippet nameCell(domain: Domain)}
  <a href="/domains/{domain.id}" class="block truncate font-medium text-text hover:text-brand"
    >{domain.name}</a
  >
{/snippet}

{#snippet companyCell(domain: Domain)}
  <span class="block truncate text-text-muted">{domain.company_name}</span>
{/snippet}

{#snippet statusCell(domain: Domain)}
  <!-- `inline-block`, not `block`: the pill has to hug its label rather than paint across the
       whole cell, and an inline box would ignore the truncate entirely. -->
  <span
    class="inline-block max-w-full truncate rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
  >
    {t(`domains.status.${domain.status}`)}
  </span>
{/snippet}

{#snippet registrarCell(domain: Domain)}
  <span class="block truncate text-text-muted">{domain.registrar_provider_name ?? "—"}</span>
{/snippet}

{#snippet dnsCell(domain: Domain)}
  <span class="block truncate text-text-muted">{domain.dns_provider_name ?? "—"}</span>
{/snippet}

{#snippet dnssecCell(domain: Domain)}
  <!-- Three states, like the detail page: never checked ≠ off (#92). -->
  <span class="block truncate text-text-muted">
    {domain.dnssec == null
      ? t("domains.dns.unknown")
      : domain.dnssec
        ? t("common.yes")
        : t("common.no")}
  </span>
{/snippet}

{#snippet emailCell(domain: Domain)}
  <span class="block truncate text-text-muted"
    >{domain.email_enabled ? t("common.yes") : t("common.no")}</span
  >
{/snippet}

{#snippet renewalCell(domain: Domain)}
  <span class="tabular-nums text-text-muted">
    {domain.next_invoice_date ? fmtNumericDate(domain.next_invoice_date) : "—"}
  </span>
{/snippet}

{#snippet registerExpiryCell(domain: Domain)}
  <!-- The registrar's own date. Highlighted only when it disagrees with what we bill on: a
       column of matching dates is noise, and the disagreement is the entire reason to open it. -->
  <span
    class="tabular-nums {domain.register_expires_on &&
    domain.register_expires_on !== domain.next_invoice_date
      ? 'font-medium text-text'
      : 'text-text-muted'}"
  >
    {domain.register_expires_on ? fmtNumericDate(domain.register_expires_on) : "—"}
  </span>
{/snippet}

{#snippet priceCell(domain: Domain)}
  <!-- Override → TLD list price → an honest dash, never a reassuring zero (docs/UX.md). -->
  <span class="tabular-nums text-text-muted">
    {domain.resolved_price != null ? fmtMoney(Number(domain.resolved_price)) : "—"}
  </span>
{/snippet}

{#snippet invoiceableCell(domain: Domain)}
  <!-- The resolved answer (#298). "Volgt register" is the interesting one: it says the
       decision is the register's, so the row changes when the register does. -->
  <span class="flex min-w-0 items-center gap-1 overflow-hidden text-text-muted">
    <!-- The answer is two letters and is what the column is for, so it never gives way; the
         badge behind it is the part that ellipsizes when the column is dragged narrow. -->
    <span class="shrink-0">{domain.invoiceable_effective ? t("common.yes") : t("common.no")}</span>
    {#if domain.invoiceable == null}
      <span class="min-w-0 truncate rounded-md bg-surface px-1.5 py-0.5 text-xs">
        {t("domains.invoiceable.from_register")}
      </span>
    {/if}
  </span>
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
      <span class="block truncate font-medium text-text">{domain.name}</span>
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
    <!-- Under a filter, "nog geen domeinen" is false and sends the reader looking for the
         wrong problem: the register is fine, the filter is what emptied it. -->
    <p class="font-medium text-text">
      {filtering ? t("common.no_results") : t("domains.empty")}
    </p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("domains", t("domains.title")))}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <h1 class="text-xl font-semibold text-text">{navLabel("domains", t("domains.title"))}</h1>
  {#if canWrite}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
      onclick={() => (showCreate = true)}>{t("domains.new")}</button
    >
  {/if}
</div>

<FilterBar filters={filterDefs} idPrefix="domain-filter">
  {#snippet actions()}
    <!-- Export carries what the screen is narrowed by, so the file *is* the list on screen,
         whole (docs/UX.md) — the API declares exactly these on the export route. -->
    <ImpexBar
      entity="domain"
      readPermission="domains.domain.read"
      writePermission="domains.domain.write"
      filters={{
        q: data.filters.q,
        company_id: data.filters.company,
        status: data.filters.status,
        registrar_provider_id: data.filters.registrar,
        dns_provider_id: data.filters.dns,
        invoiceable: data.filters.invoiceable,
        sort: data.table.sort,
      }}
      locale={data.locale}
      {form}
    />
    <!-- The personal column picker: every sort is reachable from here too (docs/UX.md). -->
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always: it is the only control here that changes what the *rows*
         do rather than what the list shows, so it sits after Kolommen rather than among the
         list's own controls. Pressing it opens the selection strip above the table. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  {/snippet}
</FilterBar>

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

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
  {selecting}
  bind:selected={bulkSelected}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

{#if canWrite}
  <Modal bind:open={showCreate} title={t("domains.new")}>
    <form
      method="POST"
      action="?/create"
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") showCreate = false;
        void update({ reset: false });
      })}
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
        tldPrices={data.tldPrices}
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
        <Button loading={busy.active}>{t("common.save")}</Button>
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
