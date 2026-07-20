<script lang="ts">
  import { applyAction, enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Download, Pencil, Trash2, Upload } from "@lucide/svelte";

  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImportCsvModal from "$lib/core/impex/ImportCsvModal.svelte";
  import { can } from "$lib/core/permissions";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";
  import { COMPANY_COLUMNS, HOURS_COLUMN } from "$lib/modules/companies/columns";
  import { COMPANY_STATUSES, statusPillClass } from "$lib/modules/companies/status";
  import ContactDraftField from "$lib/modules/contacts/ContactDraftField.svelte";

  let { data, form } = $props();

  type Company = (typeof data.companies)[number];

  let showCreate = $state(false);

  // The create form's lookups stream in behind the list. Held in state rather than awaited in the
  // markup: a re-run load hands us a *new* promise, and an `{#await}` would fall back to its
  // pending branch and remount the form, throwing away the contacts the user had picked.
  let createForm = $state<Awaited<typeof data.createForm> | null>(null);
  $effect(() => {
    void data.createForm.then((resolved) => (createForm = resolved));
  });

  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);
  let showImport = $state(false);

  // The Export link carries the page's current filters, so the file holds exactly the
  // filtered list on screen — the whole set, not just the loaded page (issue #77).
  const exportHref = $derived.by(() => {
    const params = new URLSearchParams();
    const q = page.url.searchParams.get("q");
    if (q) params.set("q", q);
    if (data.statusFilter) params.set("status", data.statusFilter);
    if (data.mine) params.set("mine", "1");
    if (data.table.sort) params.set("sort", data.table.sort);
    const query = params.toString();
    return `/companies/export${query ? `?${query}` : ""}`;
  });

  // --- columns ---------------------------------------------------------------
  // The tenant's custom fields join the built-ins as selectable columns with no code here — that
  // is the whole point of the descriptor list (#24). Everything else — resolving the saved
  // layout, persisting a change, deciding whether a change means the server must recompute — is
  // the shared table layout's job.
  const allColumns = $derived([
    ...COMPANY_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);

  const table = createTableLayout<Company>({
    all: () => allColumns,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      website: websiteCell,
      status: statusCell,
      assignees: assigneesCell,
      hours: hoursCell,
      created_at: createdCell,
    }),
    // Showing the budget roll-up means the API must compute it; hiding it means it must not.
    reloadOn: [HOURS_COLUMN],
  });

  const filtered = $derived(
    data.statusFilter
      ? data.companies.filter((c) => c.status === data.statusFilter)
      : data.companies,
  );

  function setStatusFilter(status: string) {
    const url = new URL(page.url);
    if (status && status !== data.statusFilter) url.searchParams.set("status", status);
    else url.searchParams.delete("status");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // Unlike the status pills, "my clients" is filtered server-side — the list is paginated, so a
  // client-side filter would only ever narrow the page you happen to be on.
  function toggleMine() {
    const url = new URL(page.url);
    if (data.mine) url.searchParams.delete("mine");
    else url.searchParams.set("mine", "1");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  function confirmDeleteOf(company: Company) {
    deleteId = company.id;
    deleteName = company.name;
    confirmDelete = true;
  }
</script>

{#snippet nameCell(company: Company)}
  <a href="/companies/{company.id}" class="font-medium text-text hover:text-brand">{company.name}</a
  >
{/snippet}

{#snippet websiteCell(company: Company)}
  {#if company.website}
    <span class="truncate text-text-muted">{company.website}</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet statusCell(company: Company)}
  <span class="rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(company.status)}">
    {t(`companies.status.${company.status}`)}
  </span>
{/snippet}

{#snippet assigneesCell(company: Company)}
  <AvatarStack assignees={company.assignees ?? []} members={data.members} />
{/snippet}

{#snippet hoursCell(company: Company)}
  <HoursCell hours={company.hours} />
{/snippet}

{#snippet createdCell(company: Company)}
  <span class="text-text-muted">{fmtNumericDate(company.created_at.slice(0, 10))}</span>
{/snippet}

{#snippet rowActions(company: Company)}
  <ActionsMenu
    items={[
      { label: t("common.edit"), icon: Pencil, href: editHref(`/companies/${company.id}`) },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => confirmDeleteOf(company),
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(company: Company)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/companies/{company.id}" class="min-w-0 flex-1">
      <span class="font-medium text-text">{company.name}</span>
      {#if table.visibleKeys.includes("hours") && company.hours}
        <span class="mt-0.5 block text-xs"><HoursCell hours={company.hours} /></span>
      {:else if company.website}
        <span class="mt-0.5 block truncate text-sm text-text-muted">{company.website}</span>
      {/if}
    </a>
    <span
      class="shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(
        company.status,
      )}"
    >
      {t(`companies.status.${company.status}`)}
    </span>
    {@render rowActions(company)}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("companies.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("companies.empty_hint")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("companies", t("companies.title")))}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("companies", t("companies.title"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("companies.count", { count: data.total })}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("companies.new")}
  </button>
</div>

<!-- Search + status filter pills + the personal column picker -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput placeholder={t("companies.search_placeholder")} />
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.mine
      ? 'bg-brand/10 text-brand ring-2 ring-brand'
      : 'bg-surface text-text-muted hover:text-text'}"
    aria-pressed={data.mine}
    onclick={toggleMine}>{t("companies.filter.mine")}</button
  >
  {#each COMPANY_STATUSES as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'ring-2 ring-brand ' + statusPillClass(status)
        : statusPillClass(status) + ' opacity-70 hover:opacity-100'}"
      onclick={() => setStatusFilter(status)}>{t(`companies.status.${status}`)}</button
    >
  {/each}
  {#if data.statusFilter}
    <button
      class="text-xs text-text-muted underline hover:text-text"
      onclick={() => setStatusFilter("")}
    >
      {t("tasks.filter.clear")}
    </button>
  {/if}
  <div class="ml-auto flex flex-wrap items-center gap-2">
    <!-- A plain link: the browser downloads through its own session (issue #77). -->
    <a
      href={exportHref}
      data-sveltekit-reload
      data-sveltekit-preload-data="off"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
    >
      <Download class="h-4 w-4" />
      {t("impex.export")}
    </a>
    {#if can(page.data.user, "companies.company.write")}
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
        onclick={() => (showImport = true)}
      >
        <Upload class="h-4 w-4" />
        {t("impex.import")}
      </button>
    {/if}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
  </div>
</div>

<ImportCsvModal
  bind:open={showImport}
  report={form?.impex ?? null}
  error={form?.impexError ?? null}
/>

{#if showCreate}
  <!-- Same field set as the edit surface (CompanyForm), plus the contact persons — which only a
       not-yet-created client needs to pick up front. -->
  {#if createForm}
    <form
      method="POST"
      action="?/create"
      use:enhance={() =>
        async ({ result, update }) => {
          if (result.type === "success") {
            await update();
            showCreate = false;
            return;
          }
          // Leave the form standing on a rejected save: closing it would take the error message
          // down with it, along with everything typed and every contact picked.
          await applyAction(result);
        }}
      class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
    >
      <CompanyForm
        members={createForm.members}
        definitions={createForm.definitions}
        locale={data.locale}
        idPrefix="new-company"
      >
        <ContactDraftField
          contacts={createForm.contacts}
          definitions={createForm.contactDefinitions}
          locale={data.locale}
        />
      </CompanyForm>
      {#if form?.error}
        <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <div class="mt-4 flex gap-2">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showCreate = false)}
        >
          {t("common.cancel")}
        </button>
      </div>
    </form>
  {:else}
    <div class="mb-6 h-64 animate-pulse rounded-xl border border-border bg-surface-raised"></div>
  {/if}
{/if}

<DataTable
  rows={filtered}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(company) => `/companies/${company.id}`}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("companies.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
