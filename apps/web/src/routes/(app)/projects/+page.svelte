<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { projectName, UNNAMED_CLASS } from "$lib/core/unnamed";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Assignees from "$lib/core/ui/Assignees.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import { HOURS_COLUMN, PROJECT_COLUMNS } from "$lib/modules/projects/columns";
  import type { ProjectFilterKey } from "$lib/modules/projects/filters";
  import {
    PROJECT_STATUS_ALL,
    PROJECT_STATUSES,
    statusPillClass,
  } from "$lib/modules/projects/status";

  let { data, form } = $props();

  type Project = (typeof data.projects)[number];

  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);

  // Row actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "projects.project.write"));
  const canDelete = $derived(can(page.data.user, "projects.project.delete"));

  // --- grouped by client -----------------------------------------------------
  // The same shape as the contacts list: the sections are the clients *on this page*,
  // alphabetically, with the clientless rows last. Built from the rows and never from the client
  // picker — that list is capped at 200 and sorted by name, so on a larger tenant it would both
  // invent empty sections and drop real projects into "Overig".
  //
  // A project has exactly one client, so `groupBy` returns one key (contacts return several,
  // because a person may sit under two clients). The Klant column stays: the heading names the
  // client, the cell is what links to it. It already carries no `sortKey` (see `columns.ts`) —
  // a sort orders rows *within* a section and never reorders the sections (docs/UX.md).
  const NO_COMPANY = "__no_company";
  const groups = $derived.by(() => {
    // A plain record, not a Map: `svelte/prefer-svelte-reactivity` rejects a mutated Map even in
    // a derived, and this one is a throwaway index rather than state.
    const named: Record<string, string> = {};
    let unattached = false;
    for (const project of data.projects) {
      // `company_name` comes from the API beside `company_id` — one batched lookup for the page.
      if (project.company_id) named[project.company_id] = project.company_name ?? "";
      else unattached = true;
    }
    const sections = Object.entries(named)
      .map(([key, label]) => ({ key, label, collapsible: true }))
      .sort((a, b) => a.label.localeCompare(b.label, data.locale));
    if (unattached)
      sections.push({ key: NO_COMPANY, label: t("projects.group.no_company"), collapsible: true });
    return sections;
  });

  const groupOf = (project: Project): string => project.company_id ?? NO_COMPANY;

  // Create-then-edit still (docs/UX.md Principle 3) — the one thing asked up front is the
  // client, because a project without one no longer exists: the API refuses it, and the
  // alternative is a record you make and then cannot save. One field, prefilled from the
  // list's own client filter, and the rest of the definition is the detail page's as before.
  const busy = new InFlight();
  let newOpen = $state(false);
  let newCompany = $state("");
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") newCompany = created.id;
  });

  // --- columns ---------------------------------------------------------------
  const allColumns = $derived([
    ...PROJECT_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);

  const table = createTableLayout<Project>({
    all: () => allColumns,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      company: companyCell,
      status: statusCell,
      assignees: assigneesCell,
      hours: hoursCell,
      budget_hours: budgetCell,
      start_date: startCell,
      end_date: endCell,
    }),
    // Showing the burn-down means the API must compute it; hiding it means it must not.
    reloadOn: [HOURS_COLUMN],
  });

  function confirmDeleteOf(project: Project) {
    deleteId = project.id;
    deleteName = project.name;
    confirmDelete = true;
  }

  // Filtered by the API — matching any assignee, not just the primary.
  // Client filter (#154) — the tasks page's URL-param shape; the API applies it. Archived
  // clients sit behind the search rather than among the live ones, and the one this list is
  // *currently* filtered by stays on offer whatever its status (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.companyFilter }),
  );
  const companyItems = $derived(companyPicker.live);
  /**
   * The list's filters, rendered by the shared bar (#354).
   *
   * Same order as every other list here: search, then the pickers, then the pills. It used to
   * open with *Mijn projecten* — the one screen whose scope chip sat to the *left* of the search
   * box — which is the kind of drift a shared bar exists to end.
   *
   * Absent status = the working set, `all` = everything (#329, see `+page.server.ts`), so the
   * default is a pill of its own and pressing the one you are on returns you to it: a filter you
   * cannot unset with the control you set it with is a trap.
   */
  const filterDefs: FilterDef<ProjectFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("projects.search_placeholder") },
    {
      kind: "select",
      key: "company",
      placeholder: t("projects.field.company"),
      options: companyItems,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    { kind: "pills", key: "mine", options: [{ value: "1", label: t("projects.filter.mine") }] },
    {
      kind: "pills",
      key: "status",
      options: [
        { value: "", label: t("projects.filter.not_archived") },
        { value: PROJECT_STATUS_ALL, label: t("projects.filter.all") },
        ...PROJECT_STATUSES.map((status) => ({
          value: status,
          label: t(`projects.status.${status}`),
          class: `${statusPillClass(status)} opacity-70 hover:opacity-100`,
        })),
      ],
    },
    // The abandoned create-then-edit rows (#350), gathered so they can be renamed or deleted.
    // Orthogonal to the status pills: a nameless project has a status like any other.
    {
      kind: "pills",
      key: "unnamed",
      options: [{ value: "1", label: t("projects.filter.unnamed") }],
    },
  ]);

  // --- bulk (the ✎ selection mode in the toolbar) --------------------------------------
  // Status, client and the billable default: the three that say how a *batch* of work is run —
  // closing out a quarter, moving an account, flipping a run of internal work to non-billable.
  // A budget is a figure agreed per project, so setting one across a selection would be wrong
  // on nearly all of them; it is deliberately absent here and in the API's descriptor.
  // Mirrors `apps/api/app/modules/projects/bulk.py`; labels are the import's, so the two
  // surfaces that name the same column can never name it differently.
  //
  // Declared last because the client options are the picker's own (`companyItems`), and
  // `$derived` so a client created inline reaches both controls at once.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const bulkFields: BulkFieldDef[] = $derived([
    {
      key: "status",
      label: t("impex.column.project.status"),
      type: "select",
      options: PROJECT_STATUSES.map((status) => ({
        value: status,
        label: t(`projects.status.${status}`),
      })),
    },
    {
      key: "company",
      label: t("impex.column.project.company"),
      type: "fk",
      options: companyItems,
    },
    // The dialog draws Ja/Nee itself — a bare checkbox has no "leave this one alone" state.
    { key: "billable_default", label: t("impex.column.project.billable_default"), type: "bool" },
  ]);
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "projects.project.write",
    deletePermission: "projects.project.delete",
    deleteMessage: t("projects.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
  });
</script>

{#snippet nameCell(project: Project)}
  <!-- `block`, because `overflow` does not apply to an inline box: on a bare `<a>` the class
       would set `nowrap` and nothing else, and the name would spill into Klant. -->
  <a
    href="/projects/{project.id}"
    class="block truncate font-medium text-text hover:text-brand {project.unnamed
      ? UNNAMED_CLASS
      : ''}">{projectName(project)}</a
  >
{/snippet}

{#snippet companyCell(project: Project)}
  {#if project.company_id}
    <a
      href="/companies/{project.company_id}"
      class="block truncate text-text-muted hover:text-brand">{project.company_name}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet statusCell(project: Project)}
  <!-- Coloured like the client list's, and for the same reason: once the default view leaves the
       archive out, "which of these is still running?" is a question the row has to be able to
       answer at a glance rather than by being read. -->
  <span
    class="inline-block max-w-full truncate rounded-full px-2.5 py-0.5 align-middle text-xs font-medium
      {statusPillClass(project.status)}"
  >
    {t(`projects.status.${project.status}`)}
  </span>
{/snippet}

{#snippet assigneesCell(project: Project)}
  <div class="flex min-w-0 items-center overflow-hidden">
    <Assignees assignees={project.assignees ?? []} members={data.members} />
  </div>
{/snippet}

{#snippet hoursCell(project: Project)}
  <HoursCell hours={project.hours} />
{/snippet}

{#snippet budgetCell(project: Project)}
  {#if project.budget_hours != null}
    <span class="text-text">{fmtNumber(project.budget_hours)} u</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet startCell(project: Project)}
  {#if project.start_date}
    <span class="text-text-muted">{fmtNumericDate(project.start_date)}</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet endCell(project: Project)}
  {#if project.end_date}
    <span class="text-text-muted">{fmtNumericDate(project.end_date)}</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet rowActions(project: Project)}
  <ActionsMenu
    items={[
      ...(canWrite
        ? [{ label: t("common.edit"), icon: Pencil, href: editHref(`/projects/${project.id}`) }]
        : []),
      ...(canDelete
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => confirmDeleteOf(project),
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(project: Project)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/projects/{project.id}" class="min-w-0 flex-1">
      <!-- No client suffix here: the phone list keeps the sections, so the row already sits
           under its client's heading and repeating it costs the name its width. -->
      <span class="block truncate font-medium text-text {project.unnamed ? UNNAMED_CLASS : ''}"
        >{projectName(project)}</span
      >
      {#if table.visibleKeys.includes("hours") && project.hours}
        <span class="mt-0.5 block text-xs"><HoursCell hours={project.hours} /></span>
      {/if}
    </a>
    <span
      class="shrink-0 rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-text-muted"
    >
      {t(`projects.status.${project.status}`)}
    </span>
    {#if canWrite || canDelete}
      {@render rowActions(project)}
    {/if}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("projects.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("projects.empty_hint")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("projects", t("projects.title")))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{navLabel("projects", t("projects.title"))}</h1>
  <!-- Create-then-edit (docs/UX.md Principle 3, same as tasks #230): the server creates a
       minimal project and redirects to its detail page in edit mode — creating and editing
       share one surface instead of a duplicate inline form. -->
  {#if canWrite}
    <button
      type="button"
      class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => {
        newCompany = data.companyFilter;
        newOpen = true;
      }}
    >
      {t("projects.new")}
    </button>
  {/if}
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<FilterBar filters={filterDefs} idPrefix="project-filter">
  {#snippet actions()}
    <ImpexBar
      entity="project"
      readPermission="projects.project.read"
      writePermission="projects.project.write"
      filters={{
        q: page.url.searchParams.get("q"),
        company_id: data.companyFilter,
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
  {/snippet}
</FilterBar>

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

{#if data.total > data.paging.limit}
  <!-- Sectioned by client, "Acme (2)" above a client that has seven projects reads as the whole
       answer. The pager below says which slice this is, but the *group counts* still need saying
       out loud — a cap is reported, never silent (docs/PERFORMANCE.md). -->
  <p class="mb-3 text-sm text-text-muted">{t("projects.groups_page_only")}</p>
{/if}

<DataTable
  rows={data.projects}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  {groups}
  groupBy={groupOf}
  collapsed={table.collapsed}
  oncollapse={table.onCollapse}
  rowHref={(project) => `/projects/${project.id}`}
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

<!-- "Voor welke klant?" — the whole of the new-project question, because everything else about
     a project is edited on the record itself (create-then-edit). -->
<Modal bind:open={newOpen} title={t("projects.new")}>
  <form method="POST" action="?/create" use:enhance={busy.clear()} class="space-y-3">
    <div>
      <label for="new-project-company" class="mb-1 block text-sm font-medium text-text"
        >{t("projects.field.company")}</label
      >
      <Combobox
        items={companyItems}
        archived={companyPicker.retired}
        archivedLabel={companyArchivedLabel()}
        name="company_id"
        bind:value={newCompany}
        id="new-project-company"
        allowEmpty={false}
        placeholder={t("projects.field.company")}
        oncreate={(name) => {
          qcCompanyName = name;
          qcCompanyOpen = true;
        }}
      />
    </div>
    {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (newOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.active} disabled={!newCompany || busy.active}>
        {t("common.create")}
      </Button>
    </div>
  </form>
</Modal>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("projects.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
