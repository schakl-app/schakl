<script lang="ts">
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto, invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { customFieldColumns, resolveColumns } from "$lib/core/table/columns";
  import type { ColumnSpec } from "$lib/core/table/columns";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { PROJECT_COLUMNS } from "$lib/modules/projects/columns";

  let { data, form } = $props();

  type Project = (typeof data.projects)[number];

  let showCreate = $state(false);
  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );

  // --- columns ---------------------------------------------------------------
  const allColumns = $derived([
    ...PROJECT_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);
  const resolved = $derived(resolveColumns(allColumns, data.table.pref));
  const visibleKeys = $derived(resolved.columns.map((c) => c.key));
  const pickerColumns = $derived(
    allColumns.map((c) => ({
      key: c.key,
      label: c.label ?? t(c.labelKey ?? c.key),
      primary: c.primary,
      sortKey: c.sortKey,
    })),
  );

  const cells: Record<string, unknown> = $derived({
    name: nameCell,
    company: companyCell,
    status: statusCell,
    assignees: assigneesCell,
    hours: hoursCell,
    budget_hours: budgetCell,
    hourly_rate: rateCell,
    start_date: startCell,
    end_date: endCell,
  });

  const columns = $derived(
    resolved.columns.map((meta) => ({
      ...meta,
      label: meta.label ?? t(meta.labelKey ?? meta.key),
      cell: cells[meta.key],
    })) as ColumnSpec<Project>[],
  );

  // --- persistence -----------------------------------------------------------
  let saveForm: HTMLFormElement | undefined = $state();
  let pendingColumns = $state("");
  let pendingSort = $state("");
  let pendingWidths = $state("{}");
  let reloadAfterSave = $state(false);

  function persist(
    next: { columns?: string[]; sort?: string | null; widths?: Record<string, number> },
    reload = false,
  ) {
    pendingColumns = (next.columns ?? visibleKeys).join(",");
    pendingSort = next.sort ?? data.table.sort ?? "";
    pendingWidths = JSON.stringify(next.widths ?? resolved.widths);
    reloadAfterSave = reload;
    setTimeout(() => saveForm?.requestSubmit(), 0);
  }

  function onColumnsChange(keys: string[]) {
    // Reload: showing the hours column means the list must now ask the API for the aggregate.
    persist({ columns: keys }, true);
  }

  function onSort(next: string | null) {
    const url = new URL(page.url);
    if (next) url.searchParams.set("sort", next);
    else url.searchParams.delete("sort");
    void goto(url, { keepFocus: true, noScroll: true });
    persist({ sort: next });
  }

  function confirmDeleteOf(project: Project) {
    deleteId = project.id;
    deleteName = project.name;
    confirmDelete = true;
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // Filtered by the API — matching any assignee, not just the primary.
  function toggleMine() {
    const url = new URL(page.url);
    if (data.mine) url.searchParams.delete("mine");
    else url.searchParams.set("mine", "1");
    void goto(url, { keepFocus: true, noScroll: true });
  }
</script>

{#snippet nameCell(project: Project)}
  <a href="/projects/{project.id}" class="font-medium text-text hover:text-brand">{project.name}</a>
{/snippet}

{#snippet companyCell(project: Project)}
  {#if companyName(project.company_id)}
    <a href="/companies/{project.company_id}" class="text-text-muted hover:text-brand"
      >{companyName(project.company_id)}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet statusCell(project: Project)}
  <span class="rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-text-muted">
    {t(`projects.status.${project.status}`)}
  </span>
{/snippet}

{#snippet assigneesCell(project: Project)}
  <AvatarStack assignees={project.assignees ?? []} members={data.members} />
{/snippet}

{#snippet hoursCell(project: Project)}
  <HoursCell hours={project.hours} />
{/snippet}

{#snippet budgetCell(project: Project)}
  {#if project.budget_hours != null}
    <span class="text-text">{fmtNumber(project.budget_hours)} u</span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet rateCell(project: Project)}
  {#if project.hourly_rate != null}
    <span class="text-text">{fmtNumber(project.hourly_rate)}</span>
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
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => confirmDeleteOf(project),
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(project: Project)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/projects/{project.id}" class="min-w-0 flex-1">
      <span class="font-medium text-text">{project.name}</span>
      {#if companyName(project.company_id)}
        <span class="ml-2 text-sm text-text-muted">· {companyName(project.company_id)}</span>
      {/if}
      {#if visibleKeys.includes("hours") && project.hours}
        <span class="mt-0.5 block text-xs"><HoursCell hours={project.hours} /></span>
      {/if}
    </a>
    <span
      class="shrink-0 rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-text-muted"
    >
      {t(`projects.status.${project.status}`)}
    </span>
    {@render rowActions(project)}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("projects.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("projects.empty_hint")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{t("projects.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("projects.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("projects.count", { count: data.total })}</p>
  </div>
  <div class="ml-auto mr-3 flex items-center gap-2">
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.mine
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.mine}
      onclick={toggleMine}>{t("projects.filter.mine")}</button
    >
    <SearchInput />
    <ColumnPicker
      all={pickerColumns}
      visible={visibleKeys}
      sort={data.table.sort}
      onchange={onColumnsChange}
      onsort={onSort}
    />
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("projects.new")}
  </button>
</div>

<form
  bind:this={saveForm}
  method="POST"
  action="?/saveTable"
  class="hidden"
  use:enhance={() =>
    async ({ update }) => {
      // A layout change may change what the API must compute, so it reloads; a resize must not.
      if (reloadAfterSave) await invalidateAll();
      else await update({ reset: false, invalidateAll: false });
    }}
>
  <input type="hidden" name="columns" value={pendingColumns} />
  <input type="hidden" name="sort" value={pendingSort} />
  <input type="hidden" name="widths" value={pendingWidths} />
</form>

{#if showCreate}
  <form
    method="POST"
    action="?/create"
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => (showCreate = false));
      }}
    class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="name" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.name")}
        </label>
        <input id="name" name="name" required class={inputClass} />
      </div>
      <div>
        <label for="company_id" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.company")}
        </label>
        <select id="company_id" name="company_id" class={inputClass}>
          <option value="">{t("common.none")}</option>
          {#each data.companies as company (company.id)}
            <option value={company.id}>{company.name}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="status" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.status")}
        </label>
        <select id="status" name="status" class={inputClass}>
          {#each STATUSES as s (s)}
            <option value={s}>{t(`projects.status.${s}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <span class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.assignees")}
        </span>
        <AssigneePicker
          members={data.members}
          id="new-project-assignees"
          placeholder={t("projects.responsible_inherits")}
        />
      </div>
      <div>
        <label for="budget_hours" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.budget_hours")}
        </label>
        <input
          id="budget_hours"
          name="budget_hours"
          type="number"
          min="0"
          step="0.5"
          class={inputClass}
        />
      </div>
      <div>
        <label for="budget_amount" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.budget_amount")}
        </label>
        <input
          id="budget_amount"
          name="budget_amount"
          type="number"
          min="0"
          step="0.01"
          class={inputClass}
        />
      </div>
      <div>
        <label for="hourly_rate" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.hourly_rate")}
        </label>
        <input
          id="hourly_rate"
          name="hourly_rate"
          type="number"
          min="0"
          step="0.01"
          class={inputClass}
        />
      </div>
      <div>
        <label for="start_date" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.start_date")}
        </label>
        <input id="start_date" name="start_date" type="date" class={inputClass} />
      </div>
      <div>
        <label for="end_date" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.end_date")}
        </label>
        <input id="end_date" name="end_date" type="date" class={inputClass} />
      </div>
      <div class="flex items-center gap-2 pt-6">
        <input
          id="billable_default"
          name="billable_default"
          type="checkbox"
          checked
          class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
        />
        <label for="billable_default" class="text-sm font-medium text-text">
          {t("projects.field.billable_default")}
        </label>
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsForm definitions={data.definitions} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}
      <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
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
{/if}

<DataTable
  rows={data.projects}
  {columns}
  sort={data.table.sort}
  widths={resolved.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(project) => `/projects/${project.id}`}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={onSort}
  onresize={(widths) => persist({ widths })}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("projects.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
