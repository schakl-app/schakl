<script lang="ts">
  import { Mic, Trash2 } from "@lucide/svelte";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import Assignees from "$lib/core/ui/Assignees.svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import type { components } from "$lib/core/api/schema";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { fmtDayMonth, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { taskTitle, UNNAMED_CLASS } from "$lib/core/unnamed";
  import { splitMemberOptions } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { orgToday } from "$lib/core/today";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import BudgetBar from "$lib/core/ui/BudgetBar.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import MemberPicker from "$lib/core/ui/MemberPicker.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { taskBurn } from "$lib/modules/tasks/budget";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { TASK_COLUMNS } from "$lib/modules/tasks/columns";
  import { DUE_BUCKETS } from "$lib/modules/tasks/due";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import {
    defaultStatusKey,
    statusGroups,
    terminalKeys,
    terminalStatusKey,
  } from "$lib/modules/tasks/statuses";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskDictateSheet from "$lib/modules/tasks/TaskDictateSheet.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";
  import TaskRow from "$lib/modules/tasks/TaskRow.svelte";
  import TasksNav from "$lib/modules/tasks/TasksNav.svelte";
  import { formatMinutes } from "$lib/modules/time/format";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";

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

  // Dictating a task (#382). The sheet gates itself on all three conditions — the org can
  // transcribe, this browser can record, the caller may create — and resolves the middle one
  // after mount; the opener mirrors the two the page already knows, so it is never drawn on a
  // tenant with no speech provider. `micSupported` is the sheet's to answer.
  let dictating = $state(false);
  // Who the dialog opens with on the roster: yourself, the way this button has always assigned
  // its rows — but as a chip that can be taken off, not a decision made off screen.
  const me = $derived((page.data.user?.id as string | undefined) ?? "");
  // The name is asked for before the row exists (#391): `Nieuwe taak` opens the same dialog every
  // picker's inline-create opens, and its action redirects into edit mode for the rest.
  let creating = $state(false);
  const canDictate = $derived(
    canCreate && aiEnabled(page.data.user, "task_assist") && aiEnabled(page.data.user, "speech"),
  );
  const canDelete = $derived(can(page.data.user, "tasks.task.delete"));

  // The four urgency buckets the dashboard tile and the board draw their sections from
  // (`$lib/modules/tasks/due`) — a partition, so picking one narrows to exactly the rows that
  // section counted, and the four together are the whole list.
  const dueOptions = DUE_BUCKETS;

  const today = orgToday();

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

  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const isOverdue = (task: Task) => !isDone(task) && !!task.due_date && task.due_date < today;

  // The two lookup filters and the ✎ dialog read one split each: an archived client and a
  // finished project stay reachable by typing rather than being suggested, and whichever is
  // currently filtering is always on offer (`core/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.filters.company_id }),
  );
  const projectPicker = $derived(
    splitProjectOptions(data.projects, { selectedId: data.filters.project_id }),
  );
  const companyItems = $derived(companyPicker.live);
  const projectItems = $derived(projectPicker.live);
  // Deactivated colleagues sit behind the search, exactly as archived clients and finished
  // projects do two lines up — the filter is a `MemberPicker`, which keeps the retired bucket
  // because "what was she holding when she left" is a question a filter exists to answer. The
  // bulk dialog below is a list of options rather than a picker, and is handed `live` only,
  // which is how its client and project fields already behave: a batch hands out new work, and
  // an account that cannot sign in is never the right end of it.
  const memberItems = $derived(splitMemberOptions(data.members).live);

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
    // Settable across a selection — pushing a week's deadlines, and how a backlog carried
    // into #392 gets dated — and **not** clearable: a task with no deadline stopped being a
    // real state, and a dialog that opens blank over rows that disagree could never tell
    // "I did not fill this in" from "empty it on all of them".
    { key: "due_date", label: t("impex.column.task.due_date"), type: "date", clearable: false },
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

<PageHeader title={navLabel("tasks", t("tasks.title"))}>
  {#snippet subtitle()}
    <!-- The total is the pager's (#334); overdue is not a count of the list, it is a warning
         about part of it, so it keeps its place under the heading — and since #404 it says so
         with the palette's glyph as well as its colour, because "late" is the one claim this
         page makes and a red word alone is not one every reader can see. -->
    {#if overdueCount > 0}
      <StateMark state="late" label={t("tasks.overdue_count", { count: overdueCount })} />
    {/if}
  {/snippet}
  <!-- Ask for the name, then create-then-edit for the rest (#391, #230): the dialog posts a
       named task and the action redirects to its detail page in edit mode, so creating and
       editing still share one surface (docs/UX.md Principle 3). Beside it, the other way in
       (#382): a task spoken in one breath, reviewed whole. Not a menu item — this is a primary
       create path, not a variant of one — and on a phone it is the reachable pair the FAB rule
       asks for. -->
  {#snippet actions()}
    {#if canCreate}
      {#if canDictate}
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
          onclick={() => (dictating = true)}
          title={t("tasks.dictate.title")}
        >
          <Mic size={15} />
          <span class="hidden sm:inline">{t("tasks.dictate.open")}</span>
        </button>
      {/if}
      <button
        type="button"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => (creating = true)}
      >
        {t("tasks.new")}
      </button>
    {/if}
  {/snippet}
</PageHeader>

{#if canDictate}
  <TaskDictateSheet
    bind:open={dictating}
    companies={data.companies}
    projects={data.projects}
    labels={data.labels}
    statuses={data.statuses}
    members={data.members}
    companyId={data.filters.company_id ?? null}
    projectId={data.filters.project_id ?? null}
  />
{/if}

{#if canCreate}
  <!-- The list's own filters ride along, so a task made while looking at one client lands on
       that client (#391) — the same carry-through the dictation sheet already does. -->
  <TaskQuickCreate
    bind:open={creating}
    companyId={data.filters.company_id ?? null}
    projectId={data.filters.project_id ?? null}
    members={data.members}
    assignees={me ? [{ user_id: me, is_primary: true }] : []}
    action="?/create"
    error={form?.error ?? null}
    pickerSlot="tasks_new"
  />
{/if}

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
      archived={companyPicker.retired}
      archivedLabel={companyArchivedLabel()}
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
      archived={projectPicker.retired}
      archivedLabel={projectArchivedLabel()}
      name="_filter_project"
      value={data.filters.project_id ?? ""}
      placeholder={t("tasks.field.project")}
      onselect={(v) => setFilter("project_id", v)}
      id="filter-project"
    />
  </div>
  {#if !isPortal}
    <div class="w-full sm:w-44">
      <MemberPicker
        members={data.members}
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
  <!-- The abandoned create-then-edit rows (#350). Reachable, so they can be renamed or
       deleted; without it they sit among real work with nothing to gather them by. -->
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.filters.unnamed
      ? 'bg-brand text-white'
      : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
    onclick={() => setFilter("unnamed", data.filters.unnamed ? "" : "1")}
    >{t("tasks.filter.unnamed")}</button
  >
  <!-- The rows an instance carried into #392, where the deadline became required. Findable so
       they can be dated — one at a time, or as a selection through the ✎ beside this list. -->
  <button
    class="rounded-full px-3 py-1 text-xs font-medium
      {data.filters.undated
      ? 'bg-brand text-white'
      : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
    onclick={() => setFilter("undated", data.filters.undated ? "" : "1")}
    >{t("tasks.filter.undated")}</button
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
    <!-- A task nobody named reads as unnamed, in the *reader's* language, and is marked as
         unfinished rather than passed off as a title (#350). -->
    <a
      href="/tasks/{task.id}"
      class="truncate font-medium {done
        ? 'text-text-muted line-through'
        : 'text-text hover:text-brand'} {task.unnamed ? UNNAMED_CLASS : ''}">{taskTitle(task)}</a
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
  {@const roster = task.assignees ?? []}
  {#if roster.length > 0}
    <!-- `Assignees` is `inline-flex`, so on its own it takes its min-content width and spills past
         the column; as a flex item it shrinks and its chips' own `truncate` has room to work.
         `max=1` keeps the row one line high whatever the roster: the verantwoordelijke is named
         and the rest are a `+N` that names them in its tooltip (#375). -->
    <span class="flex min-w-0 items-center">
      <Assignees assignees={roster} members={data.members} />
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

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

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
  {selecting}
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
