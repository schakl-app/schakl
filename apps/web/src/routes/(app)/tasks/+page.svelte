<script lang="ts">
  import { Trash2 } from "@lucide/svelte";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import PersonChip from "$lib/core/ui/PersonChip.svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import type { components } from "$lib/core/api/schema";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { fmtDayMonth, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import BudgetBar from "$lib/core/ui/BudgetBar.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { taskBurn } from "$lib/modules/tasks/budget";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { TASK_COLUMNS } from "$lib/modules/tasks/columns";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import {
    defaultStatusKey,
    statusGroups,
    terminalKeys,
    terminalStatusKey,
  } from "$lib/modules/tasks/statuses";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskRow from "$lib/modules/tasks/TaskRow.svelte";
  import TasksNav from "$lib/modules/tasks/TasksNav.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  let { data, form } = $props();

  type Task = (typeof data.tasks)[number];

  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Actions render only for holders of the matching permission (#253). The complete toggle is a
  // task-status write (PATCH /api/v1/tasks/{id}), so it mirrors `tasks.task.write` — a read-only
  // portal client (#244) sees a static status marker, never a clickable checkbox. That check is
  // per *row* (`canWriteTask`, below in `titleCell`), because `:own` means assignee: the seeded
  // `member` role would otherwise get a live checkbox on all forty of their colleagues' tasks.
  const canCreate = $derived(can(page.data.user, "tasks.task.create"));
  const canDelete = $derived(can(page.data.user, "tasks.task.delete"));

  const dueOptions = ["overdue", "today", "week"] as const;

  const today = new Date().toISOString().slice(0, 10);

  // The status vocabulary is per-org (issue #62): "finished" is a terminal status, not the literal
  // "done", and the complete toggle moves between the default and the first terminal status.
  const terminalSet = $derived(new Set(terminalKeys(data.statuses)));
  const isDone = (task: Task) => terminalSet.has(task.status);
  const toggleTarget = (task: Task) =>
    isDone(task) ? defaultStatusKey(data.statuses) : terminalStatusKey(data.statuses);

  const overdueCount = $derived(
    data.tasks.filter((task) => !isDone(task) && task.due_date && task.due_date < today).length,
  );

  const table = createTableLayout<Task>({
    all: () => TASK_COLUMNS,
    // A first visit folds "Klaar" away, exactly as the old board did. Once the user has saved a
    // layout their own collapsed set wins — including an empty one, which is why this checks for
    // the key's absence rather than for a falsy value.
    pref: () => ({
      ...data.table.pref,
      // A first visit folds the finished sections away; a saved layout (even an empty one) wins.
      collapsed: data.table.pref.collapsed ?? terminalKeys(data.statuses),
    }),
    sort: () => data.table.sort,
    cells: () => ({
      title: titleCell,
      labels: labelsCell,
      assignee: assigneeCell,
      priority: priorityCell,
      due_date: dueDateCell,
      checklist: checklistCell,
      comments: commentsCell,
      allocated: allocatedCell,
      project: projectCell,
      company: companyCell,
      created_at: createdAtCell,
    }),
  });

  // Sections are declared in workflow order and the table never reorders them — a sort orders
  // rows *within* a section (#38). An empty section is dropped rather than drawn as "Klaar (0)".
  const groups = $derived(
    statusGroups(data.statuses).filter((group) =>
      data.tasks.some((task) => task.status === group.key),
    ),
  );

  const memberName = (id?: string | null) => {
    const member = data.members.find((m) => m.user_id === id);
    return member ? memberLabel(member) : "";
  };
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const isOverdue = (task: Task) => !isDone(task) && !!task.due_date && task.due_date < today;

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const projectItems = $derived(data.projects.map((p) => ({ value: p.id, label: p.name })));
  const memberItems = $derived(
    data.members.map((m) => ({ value: m.user_id, label: memberLabel(m) })),
  );

  // --- bulk (the ✎ selection mode in the toolbar) --------------------------------------
  // Triage is a bulk gesture: hand a sprint to a colleague, move a run of tickets onto the
  // project they turned out to belong to, push a week of deadlines, close what is done. So this
  // list offers the six fields a batch can decide honestly — status, assignee, priority, project,
  // client, deadline (`apps/api/app/modules/tasks/bulk.py`). A title, a description or a checklist
  // is a fact about *one* task and is not offered. Every option list is one the page already
  // loaded for its own filter bar, and the statuses are the org's own vocabulary (#62), never a
  // frozen list.
  //
  // A row the write refuses — a due date moved later without a reason, a terminal status still
  // owing its contact moment (#157), a colleague's task under a `:own` grant — comes back in the
  // result banner with its own key. The other forty-nine still land; a batch is not all-or-nothing.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  // The API's own `TaskPriority` (`apps/api/app/modules/tasks/models.py`), so a renamed value
  // fails to compile here rather than posting a priority the write rejects.
  const priorities: components["schemas"]["TaskPriority"][] = ["low", "normal", "high"];
  const bulkFields: BulkFieldDef[] = $derived([
    {
      key: "status",
      label: t("impex.column.task.status"),
      type: "select",
      options: data.statuses.map((status) => ({ value: status.key, label: status.name })),
    },
    {
      key: "assignee",
      label: t("impex.column.task.assignee"),
      type: "fk",
      options: memberItems,
    },
    {
      key: "priority",
      label: t("impex.column.task.priority"),
      type: "select",
      options: priorities.map((priority) => ({
        value: priority,
        label: t(`tasks.priority.${priority}`),
      })),
    },
    { key: "project", label: t("impex.column.task.project"), type: "fk", options: projectItems },
    { key: "company", label: t("impex.column.task.company"), type: "fk", options: companyItems },
    // The one clearable field here: a task that loses its deadline is a real state, not a gap.
    { key: "due_date", label: t("impex.column.task.due_date"), type: "date", clearable: true },
  ]);
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "tasks.task.write",
    deletePermission: "tasks.task.delete",
    deleteMessage: t("tasks.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
  });

  function setFilter(key: string, value: string) {
    const url = resetPage(new URL(page.url));
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // The person switcher defaults to yourself (an absent `assignee_user_id` resolves to
  // `event.locals.user.id` server-side); reflect that resolved value here too, so the picker
  // shows you pre-selected rather than empty. Explicitly clearing it writes `ALL_ASSIGNEES`
  // instead of deleting the param — deleting it would just resolve back to yourself.
  //
  // A client-portal login has no such default (the load skips it: a client is never an employee
  // assignee), so this mirrors that rather than pre-selecting a person whose tasks do not exist.
  // The control itself is staff-only below — picking "who at the agency is on it" is not a client
  // filter, and `/members/lookup` is the agency's roster.
  const isPortal = $derived(page.data.user?.isPortal ?? false);
  const assigneeFilterValue = $derived(
    data.filters.assignee_user_id === ALL_ASSIGNEES || isPortal
      ? ""
      : (data.filters.assignee_user_id ?? page.data.user?.id ?? ""),
  );
  function setAssigneeFilter(value: string) {
    setFilter("assignee_user_id", value || ALL_ASSIGNEES);
  }
  const hasFilters = $derived(Object.values(data.filters).some(Boolean));
  const activeFilterCount = $derived(Object.values(data.filters).filter(Boolean).length);
  // On a phone the filter bar collapses behind one toggle: five stacked controls otherwise push
  // the actual tasks a full screen down. Open when arriving with a filter in the URL.
  // svelte-ignore state_referenced_locally
  let showFilters = $state(Object.values(data.filters).some(Boolean));
</script>

<svelte:head>
  <title>{pageTitle(navLabel("tasks", t("tasks.title")))}</title>
</svelte:head>

<TasksNav />

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("tasks", t("tasks.title"))}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("tasks.count", { count: data.total })}
      {#if overdueCount > 0}
        · <span class="font-medium text-red-600 dark:text-red-400"
          >{t("tasks.overdue_count", { count: overdueCount })}</span
        >
      {/if}
    </p>
  </div>
  <!-- Create-then-edit (#230): the server creates a minimal task and redirects to its detail
       page in edit mode — creating and editing share one surface (docs/UX.md Principle 3). -->
  {#if canCreate}
    <form method="POST" action="?/create" use:enhance>
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("tasks.new")}
      </button>
    </form>
  {/if}
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<!-- Filter bar. Collapsed behind one toggle below `sm` (docs/UX.md: a phone is not a smaller
     desktop) — always expanded from `sm` up. -->
<button
  type="button"
  class="mb-2 flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text sm:hidden {showFilters
    ? 'border-brand text-brand'
    : ''}"
  aria-expanded={showFilters}
  onclick={() => (showFilters = !showFilters)}
>
  {t("tasks.filter.toggle")}
  {#if activeFilterCount > 0}
    <span class="rounded-full bg-brand px-1.5 py-0.5 text-[10px] font-semibold text-white"
      >{activeFilterCount}</span
    >
  {/if}
</button>
<div class="mb-4 flex-wrap items-center gap-2 {showFilters ? 'flex' : 'hidden'} sm:flex">
  <SearchInput placeholder={t("tasks.search_placeholder")} />
  <div class="w-full sm:w-44">
    <Combobox
      items={companyItems}
      name="_filter_company"
      value={data.filters.company_id ?? ""}
      placeholder={t("tasks.field.company")}
      onselect={(v) => setFilter("company_id", v)}
      id="filter-company"
    />
  </div>
  <div class="w-full sm:w-44">
    <Combobox
      items={projectItems}
      name="_filter_project"
      value={data.filters.project_id ?? ""}
      placeholder={t("tasks.field.project")}
      onselect={(v) => setFilter("project_id", v)}
      id="filter-project"
    />
  </div>
  {#if !isPortal}
    <div class="w-full sm:w-44">
      <Combobox
        items={memberItems}
        name="_filter_assignee"
        value={assigneeFilterValue}
        placeholder={t("tasks.field.assignee")}
        onselect={setAssigneeFilter}
        id="filter-assignee"
      />
    </div>
  {/if}
  {#each dueOptions as option (option)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.due === option
        ? 'bg-brand text-white'
        : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
      onclick={() => setFilter("due", data.filters.due === option ? "" : option)}
      >{t(`tasks.due.${option}`)}</button
    >
  {/each}
  <!-- The dashboard tile's "no client or project" bucket arrives here as `?unlinked=1`; the chip
       is what makes that a visible filter rather than a silently narrowed list. -->
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.filters.unlinked
      ? 'bg-brand text-white'
      : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
    onclick={() => setFilter("unlinked", data.filters.unlinked ? "" : "1")}
    >{t("tasks.filter.unlinked")}</button
  >
  {#each data.labels as label (label.id)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.label_id === label.id ? 'ring-2 ring-brand ' : ''}{labelChipClass(
        label.color,
      )}"
      onclick={() => setFilter("label_id", data.filters.label_id === label.id ? "" : label.id)}
      >{label.name}</button
    >
  {/each}
  {#if hasFilters}
    <a href="/tasks" class="text-xs text-text-muted underline hover:text-text"
      >{t("tasks.filter.clear")}</a
    >
  {/if}
</div>

<!-- Cells. The complete toggle stays a real <form> inside its <td>: it works with no JS, and
     `use:enhance` only upgrades it. Everything else that used to be a badge on `TaskRow` is now
     a column the user can turn off (#41). -->
{#snippet titleCell(task: Task)}
  {@const done = isDone(task)}
  <div class="flex items-center gap-2.5">
    {#if canWriteTask(page.data.user, task)}
      <form method="POST" action="?/toggle" use:enhance>
        <input type="hidden" name="id" value={task.id} />
        <input type="hidden" name="status" value={toggleTarget(task)} />
        <button
          class="flex h-5 w-5 items-center justify-center rounded border text-xs
            {done
            ? 'border-brand bg-brand text-white'
            : 'border-border text-transparent hover:border-brand'}"
          aria-label={t("tasks.toggle_done")}>✓</button
        >
      </form>
    {:else}
      <!-- Somebody else's task, or a read-only viewer (portal client, #244): the status shows,
           the toggle does not. -->
      <span
        class="flex h-5 w-5 items-center justify-center rounded border text-xs
          {done ? 'border-brand bg-brand text-white' : 'border-border text-transparent'}"
        aria-label={t("tasks.toggle_done")}>✓</span
      >
    {/if}
    <a
      href="/tasks/{task.id}"
      class="truncate font-medium {done
        ? 'text-text-muted line-through'
        : 'text-text hover:text-brand'}">{task.title}</a
    >
    <!-- Client-portal visibility rides the title cell rather than becoming a column the user can
         turn off (#41's rule): "a client is reading this" is the one piece of task metadata you
         need *before* you write in the card, and a marker that can be switched off is exactly the
         one nobody will have on the day it matters. -->
    <ClientVisibilityIcon
      visible={task.visible_to_client}
      companyId={task.company_id}
      projectId={task.project_id}
    />
  </div>
{/snippet}

{#snippet labelsCell(task: Task)}
  <!-- One row, never a wrapping stack: six labels used to make this row three lines tall, and a
       single long one wrapped inside its own chip. The chips shrink and ellipsize instead. -->
  <span class="flex min-w-0 gap-1 overflow-hidden">
    {#each task.labels ?? [] as label (label.id)}
      <span
        class="truncate rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(
          label.color,
        )}">{label.name}</span
      >
    {:else}
      <span class="text-text-muted">—</span>
    {/each}
  </span>
{/snippet}

{#snippet assigneeCell(task: Task)}
  {@const member = data.members.find((m) => m.user_id === task.assignee_user_id)}
  {#if member}
    <!-- The chip is `inline-flex`, so on its own it takes its min-content width and spills past
         the column; as a flex item it shrinks and its own `truncate` finally has room to work. -->
    <span class="flex min-w-0 items-center">
      <PersonChip
        name={member.full_name}
        email={member.email}
        avatarUrl={member.avatar_url ?? null}
      />
    </span>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet priorityCell(task: Task)}
  {#if task.priority === "high" && !isDone(task)}
    <span class="text-xs font-semibold uppercase text-red-600 dark:text-red-400"
      >{t("tasks.priority.high")}</span
    >
  {:else}
    <span class="text-text-muted">{t(`tasks.priority.${task.priority}`)}</span>
  {/if}
{/snippet}

{#snippet dueDateCell(task: Task)}
  {#if task.due_date}
    <!-- Overdue work is loudly red everywhere (docs/UX.md, principle 4). -->
    <span class={isOverdue(task) ? "font-semibold text-red-600 dark:text-red-400" : "text-text"}>
      {fmtDayMonth(task.due_date)}
    </span>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet checklistCell(task: Task)}
  {#if (task.checklist_total ?? 0) > 0}
    <span
      class={task.checklist_done === task.checklist_total
        ? "font-medium text-green-700 dark:text-green-300"
        : "text-text"}>{task.checklist_done}/{task.checklist_total}</span
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet commentsCell(task: Task)}
  <span class={task.comment_count ? "text-text" : "text-text-muted"}
    >{task.comment_count || "—"}</span
  >
{/snippet}

{#snippet allocatedCell(task: Task)}
  {@const burn = taskBurn(task)}
  {#if burn}
    <!-- "1u 30m / 3u" on the one burn scale, so the column answers "can I pick this up?" rather
         than only "how long was it meant to take?" (#313). -->
    <BudgetBar
      variant="inline"
      spent={burn.spent}
      budget={burn.budget}
      spentText={burn.spentText}
      remainingText={burn.remainingText}
    />
  {:else}
    <!-- No burn to draw: the caller may not read hours, or nothing has been logged against an
         unbudgeted task. Either way the plain allocation is the whole answer. -->
    <span class={task.allocated_minutes ? "text-text" : "text-text-muted"}>
      {task.allocated_minutes ? formatMinutes(task.allocated_minutes) : "—"}
    </span>
  {/if}
{/snippet}

{#snippet projectCell(task: Task)}
  {@const name = projectName(task.project_id)}
  {#if name}
    <a href="/projects/{task.project_id}" class="block truncate text-text hover:text-brand"
      >{name}</a
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet companyCell(task: Task)}
  {@const name = companyName(task.company_id)}
  {#if name}
    <a href="/companies/{task.company_id}" class="block truncate text-text hover:text-brand"
      >{name}</a
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet createdAtCell(task: Task)}
  <span class="text-text-muted">{fmtNumericDate(task.created_at.slice(0, 10))}</span>
{/snippet}

<!-- A row that represents an editable record carries a ⋯ menu; the title link is how you open
     the card, and Delete confirms (docs/UX.md). -->
{#snippet rowActions(task: Task)}
  <ActionsMenu
    compact
    items={[
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = task.id;
          confirmDelete = true;
        },
      },
    ]}
  />
{/snippet}

<!-- A grid is not a mobile UI: below `sm` the board falls back to the concept's shared row. -->
{#snippet mobileRow(task: Task)}
  <div class="flex items-center">
    <div class="min-w-0 flex-1">
      <TaskRow {task} members={data.members} statuses={data.statuses} {today} />
    </div>
    {#if canDelete}
      {@render rowActions(task)}
    {/if}
  </div>
{/snippet}

{#snippet empty()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("tasks.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("tasks.empty_hint")}</p>
  </div>
{/snippet}

<!-- The picker stays reachable even when a filter empties the board — the sort that emptied it
     is cycled off from here. -->
<div class="mb-2 flex flex-wrap items-center justify-end gap-2">
  <ImpexBar
    entity="task"
    readPermission="tasks.task.read"
    writePermission="tasks.task.write"
    filters={{
      q: data.filters.q,
      company_id: data.filters.company_id,
      project_id: data.filters.project_id,
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

<BulkBar {selecting} selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.tasks}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  {groups}
  groupBy={(task) => task.status}
  collapsed={table.collapsed}
  actions={canDelete ? rowActions : undefined}
  {mobileRow}
  {empty}
  selectable={selecting}
  bind:selected={bulkSelected}
  oncollapse={table.onCollapse}
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
  message={t("tasks.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
