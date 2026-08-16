<script lang="ts">
  import { applyAction, enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Pencil, Trash2 } from "@lucide/svelte";

  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { can } from "$lib/core/permissions";
  import { formatPhone } from "$lib/core/phone";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Assignees from "$lib/core/ui/Assignees.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";
  import { COMPANY_COLUMNS, HOURS_COLUMN } from "$lib/modules/companies/columns";
  import {
    COMPANY_STATUS_ALL,
    COMPANY_STATUSES,
    statusPillClass,
  } from "$lib/modules/companies/status";
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
  const busy = new InFlight();

  // Row actions render only for holders of the matching permission (#253) — the API refuses
  // them anyway; this stops a client-role login seeing buttons that only 403.
  const canWrite = $derived(can(page.data.user, "companies.company.write"));
  const canDelete = $derived(can(page.data.user, "companies.company.delete"));

  // --- bulk (the ✎ selection mode in the toolbar) ----------------------------
  // Only the status: everything else on a client is a fact about *that* client, and a control
  // that wrote one across a selection would exist purely to be misfired. Mirrors
  // `apps/api/app/modules/companies/bulk.py`; labels are the import's, so the two surfaces
  // that name the same column can never name it differently.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const bulkFields: BulkFieldDef[] = [
    {
      key: "status",
      label: t("impex.column.company.status"),
      type: "select",
      options: COMPANY_STATUSES.map((status) => ({
        value: status,
        label: t(`companies.status.${status}`),
      })),
    },
  ];
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "companies.company.write",
    deletePermission: "companies.company.delete",
    deleteMessage: t("companies.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
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
      client_number: clientNumberCell,
      name: nameCell,
      legal_name: legalNameCell,
      website: websiteCell,
      phone: phoneCell,
      status: statusCell,
      assignees: assigneesCell,
      hours: hoursCell,
      created_at: createdCell,
    }),
    // Showing the budget roll-up means the API must compute it; hiding it means it must not.
    reloadOn: [HOURS_COLUMN],
  });

  // Every filter here is the API's — a browser-side one narrows the page you happen to hold, not
  // the list — and every one of them resets to page 1 (`paging.ts`).
  //
  // The empty token is the default view (every status but archived, #329) rather than "no filter",
  // which is why pressing the pill you are already on still returns you to it: the way back from
  // a narrowing is the same gesture it always was. "Alles" carries its own token so that view is
  // linkable (§9).
  function setStatusFilter(status: string) {
    const url = resetPage(new URL(page.url));
    if (status && status !== data.statusFilter) url.searchParams.set("status", status);
    else url.searchParams.delete("status");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  function toggleMine() {
    const url = resetPage(new URL(page.url));
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
  <a href="/companies/{company.id}" class="block truncate font-medium text-text hover:text-brand"
    >{company.name}</a
  >
{/snippet}

{#snippet clientNumberCell(company: Company)}
  <!-- Tabular figures so a column of numbers lines up; an unnumbered client reads as a dash
       rather than as an empty cell you cannot tell from a loading one. -->
  {#if company.client_number}
    <span class="block truncate font-mono text-sm tabular-nums text-text-muted"
      >{company.client_number}</span
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet legalNameCell(company: Company)}
  <!-- A dash where the client has none, exactly as every other optional cell reads: the column
       answers "does this client invoice under another name?", and a blank cell cannot be told
       from one that failed to load. -->
  {#if company.legal_name}
    <span class="block truncate text-text-muted">{company.legal_name}</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet websiteCell(company: Company)}
  {#if company.website}
    <span class="block truncate text-text-muted">{company.website}</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet phoneCell(company: Company)}
  {#if company.phone}
    <a href="tel:{company.phone}" class="block truncate text-text-muted hover:text-brand"
      >{formatPhone(company.phone)}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet statusCell(company: Company)}
  <!-- `inline-block`, so the pill keeps its shrink-to-fit shape and still clips: the longest
       label ("Gearchiveerd") is wider than the 120px column allows. -->
  <span
    class="inline-block max-w-full truncate rounded-full px-2.5 py-0.5 text-xs font-medium
      {statusPillClass(company.status)}"
  >
    {t(`companies.status.${company.status}`)}
  </span>
{/snippet}

{#snippet assigneesCell(company: Company)}
  <!-- Assignees is an `inline-flex`, and an inline box shrink-to-fits to its content rather than
       to the column: it needs a block flex parent before its own `min-w-0` can shrink the chip
       and let the name truncate. -->
  <div class="flex min-w-0 items-center overflow-hidden">
    <Assignees assignees={company.assignees ?? []} members={data.members} />
  </div>
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
      ...(canWrite
        ? [{ label: t("common.edit"), icon: Pencil, href: editHref(`/companies/${company.id}`) }]
        : []),
      ...(canDelete
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => confirmDeleteOf(company),
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(company: Company)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/companies/{company.id}" class="min-w-0 flex-1">
      <span class="block truncate font-medium text-text">{company.name}</span>
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
    {#if canWrite || canDelete}
      {@render rowActions(company)}
    {/if}
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
  <h1 class="text-xl font-semibold text-text">{navLabel("companies", t("companies.title"))}</h1>
  {#if canWrite}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => (showCreate = !showCreate)}
    >
      {t("companies.new")}
    </button>
  {/if}
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
  <!-- The default view is a pill of its own, not the absence of one: a list that silently leaves
       the archive out looks identical to a list that has none, and the only thing that can say
       which is a control showing itself selected. "Alles" sits beside it for the other half. -->
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.statusFilter === ''
      ? 'bg-brand/10 text-brand ring-2 ring-brand'
      : 'bg-surface text-text-muted hover:text-text'}"
    aria-pressed={data.statusFilter === ""}
    onclick={() => setStatusFilter("")}>{t("companies.filter.not_archived")}</button
  >
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.statusFilter === COMPANY_STATUS_ALL
      ? 'bg-brand/10 text-brand ring-2 ring-brand'
      : 'bg-surface text-text-muted hover:text-text'}"
    aria-pressed={data.statusFilter === COMPANY_STATUS_ALL}
    onclick={() => setStatusFilter(COMPANY_STATUS_ALL)}>{t("companies.filter.all")}</button
  >
  {#each COMPANY_STATUSES as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'ring-2 ring-brand ' + statusPillClass(status)
        : statusPillClass(status) + ' opacity-70 hover:opacity-100'}"
      aria-pressed={data.statusFilter === status}
      onclick={() => setStatusFilter(status)}>{t(`companies.status.${status}`)}</button
    >
  {/each}
  <div class="ml-auto flex flex-wrap items-center gap-2">
    <!-- The Export link carries the page's current filters, so the file holds exactly the
         filtered list on screen — the whole set, not just the loaded page (issue #77). -->
    <ImpexBar
      entity="company"
      readPermission="companies.company.read"
      writePermission="companies.company.write"
      filters={{
        q: page.url.searchParams.get("q"),
        // The resolved filter, not the URL token: the default narrows, and `?status=` on the
        // export means to the API exactly what it means to the list.
        status: data.statusQuery,
        mine: data.mine,
        sort: data.table.sort,
      }}
      locale={data.locale}
      {form}
    />
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
  </div>
</div>

{#if showCreate}
  <!-- Same field set as the edit surface (CompanyForm), plus the contact persons — which only a
       not-yet-created client needs to pick up front. -->
  {#if createForm}
    <form
      method="POST"
      action="?/create"
      use:enhance={busy.wrap("", () => async ({ result, update }) => {
        if (result.type === "success") {
          await update();
          showCreate = false;
          return;
        }
        // Leave the form standing on a rejected save: closing it would take the error message
        // down with it, along with everything typed and every contact picked.
        await applyAction(result);
      })}
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
        <Button loading={busy.active}>
          {t("common.save")}
        </Button>
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

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.companies}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(company) => `/companies/${company.id}`}
  actions={canWrite || canDelete ? rowActions : undefined}
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

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("companies.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
