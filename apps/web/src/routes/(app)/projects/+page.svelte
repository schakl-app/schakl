<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { HOURS_COLUMN, PROJECT_COLUMNS } from "$lib/modules/projects/columns";

  let { data, form } = $props();

  type Project = (typeof data.projects)[number];

  let showCreate = $state(false);
  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);

  // #81: which client is picked on the create form, so the assignee field can show the
  // verantwoordelijke it will inherit instead of an empty placeholder.
  let newProjectCompanyId = $state("");

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );

  // Pre-fill the new-project assignee picker with the selected client's primary employee. This is
  // web-only and matches the API exactly: it already falls back to the company's verantwoordelijke
  // when no roster is posted, so this just makes the inherited person visible (docs/UX.md).
  const inheritedAssignees = $derived.by(() => {
    const company = data.companies.find((c) => c.id === newProjectCompanyId);
    const primary = company?.assignees?.find((a) => a.is_primary) ?? company?.assignees?.[0];
    return primary ? [{ user_id: primary.user_id, is_primary: true }] : [];
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
      hourly_rate: rateCell,
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

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // Filtered by the API — matching any assignee, not just the primary.
  function toggleMine() {
    const url = new URL(page.url);
    if (data.mine) url.searchParams.delete("mine");
    else url.searchParams.set("mine", "1");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // Client filter (#154) — the tasks page's URL-param shape; the API applies it.
  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
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
      { label: t("common.edit"), icon: Pencil, href: editHref(`/projects/${project.id}`) },
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
      {#if table.visibleKeys.includes("hours") && project.hours}
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
  <title>{pageTitle(t("projects.title"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("projects.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("projects.count", { count: data.total })}</p>
  </div>
  <button
    class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("projects.new")}
  </button>
</div>

<!-- Filter + search + the personal column picker, on their own wrapping row (issue #36): title,
     a fixed 224px search box, the picker and the primary action on one unwrappable line have a
     min-content width no phone has. This is the shape `companies` already uses. -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.mine
      ? 'bg-brand/10 text-brand ring-2 ring-brand'
      : 'bg-surface text-text-muted hover:text-text'}"
    aria-pressed={data.mine}
    onclick={toggleMine}>{t("projects.filter.mine")}</button
  >
  <SearchInput />
  <div class="w-44">
    <Combobox
      items={companyItems}
      name="_filter_company"
      value={data.companyFilter}
      placeholder={t("projects.filter.company")}
      onselect={(v) => setFilter("company", v)}
      id="filter-company"
    />
  </div>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

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
        <select id="company_id" name="company_id" bind:value={newProjectCompanyId} class={inputClass}>
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
        <!-- Remount on a client change so the picker re-seeds from `value` (it reads `value`
             once at mount, #81). The placeholder still shows when no client is picked. -->
        {#key newProjectCompanyId}
          <AssigneePicker
            members={data.members}
            value={inheritedAssignees}
            id="new-project-assignees"
            placeholder={t("projects.responsible_inherits")}
          />
        {/key}
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
        <DateInput id="start_date" name="start_date" />
      </div>
      <div>
        <label for="end_date" class="mb-1 block text-sm font-medium text-text">
          {t("projects.field.end_date")}
        </label>
        <DateInput id="end_date" name="end_date" />
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
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(project) => `/projects/${project.id}`}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("projects.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
