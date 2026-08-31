<script lang="ts">
  import { Mic, Trash2 } from "@lucide/svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import { filterUrl, type FilterDef } from "$lib/core/filters/types";
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
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { taskTitle, UNNAMED_CLASS } from "$lib/core/unnamed";
  import { splitMemberOptions } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { orgToday } from "$lib/core/today";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import BudgetBar from "$lib/core/ui/BudgetBar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import MemberPicker from "$lib/core/ui/MemberPicker.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { taskBurn } from "$lib/modules/tasks/budget";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { TASK_COLUMNS } from "$lib/modules/tasks/columns";
  import {
    DUE_BUCKETS,
    DUE_SECTIONS,
    DUE_STATE,
    dueSection,
    weekEnd,
  } from "$lib/modules/tasks/due";
  import DueDate from "$lib/modules/tasks/DueDate.svelte";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { TASK_GROUPINGS } from "$lib/modules/tasks/grouping";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import { priorityRailClass } from "$lib/modules/tasks/priority";
  import {
    defaultStatusKey,
    statusGroups,
    terminalKeys,
    terminalStatusKey,
  } from "$lib/modules/tasks/statuses";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskDictateSheet from "$lib/modules/tasks/TaskDictateSheet.svelte";
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
  // `Nieuwe taak` is create-then-edit again: one click, one placeholder row, straight into edit
  // mode on the detail page. `busy` is only here so the button can spin — the form carries
  // nothing but hidden fields, so there is nothing for a reset to blank.
  const creating = new InFlight();
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

  // --- what the board is grouped by (#395) ---------------------------------------------- #
  // Deadline by default: four urgency sections, in the order somebody actually works through
  // them. Status is the other view, one click away, and it is the board this page has always
  // been. The token lives in the URL, so the view is linkable and the back button undoes a
  // switch (`lib/modules/tasks/grouping.ts`).
  const byDue = $derived(data.grouping === "due");
  // Computed once for the page rather than per row: a list of 200 rows would run the same day
  // arithmetic 200 times for one answer that cannot change between them.
  const dueEnd = $derived(weekEnd(today));
  const sectionOf = (task: Task) => dueSection(task.due_date, today, isDone(task), dueEnd);

  // Sections are declared in reading order and the table never reorders them — a sort orders
  // rows *within* a section (#38). An empty section is dropped rather than drawn as "Later (0)":
  // an empty bucket is not news, and four headings over one task would be all heading.
  const dueGroups = $derived(
    DUE_SECTIONS.filter((key) => data.tasks.some((task) => sectionOf(task) === key)).map((key) => ({
      key,
      label: t(`tasks.due.${key}`),
      collapsible: true,
      // The heading carries the colour and the rows stay quiet: the hierarchy the team asked
      // for is *between* the groups, and twenty tinted rows would be worse than twenty grey
      // ones. It is the state palette's, glyph and all — never the tenant's brand (#404).
      // `undefined` where the section has no state at all, which is how *Later* and *Afgerond*
      // keep the muted heading every grouped list has always had.
      state: DUE_STATE[key] ?? undefined,
    })),
  );
  const statusGroupsPresent = $derived(
    statusGroups(data.statuses).filter((group) =>
      data.tasks.some((task) => task.status === group.key),
    ),
  );
  const groups = $derived(byDue ? dueGroups : statusGroupsPresent);

  // The warning under the heading is the *Over tijd* section counted, and it says so by asking
  // the same helper: a page that computes "late" twice is a page that can print two numbers.
  const overdueCount = $derived(data.tasks.filter((task) => sectionOf(task) === "overdue").length);

  function groupHref(grouping: string): string {
    const url = new URL(page.url);
    // The default is the absent token, so switching back to it leaves a clean URL rather than
    // one that pins today's default into every link somebody pastes.
    if (grouping === "due") url.searchParams.delete("group");
    else url.searchParams.set("group", grouping);
    return url.pathname + url.search;
  }

  const table = createTableLayout<Task>({
    all: () => TASK_COLUMNS,
    // A first visit folds the finished work away, exactly as the old board did. Once the user has
    // saved a layout their own collapsed set wins — including an empty one, which is why this
    // checks for the key's absence rather than for a falsy value.
    //
    // Which key that *is* depends on what the board is grouped by: the tenant's terminal statuses
    // under Status, the one `done` section under Deadline. One saved list serves both, and a key
    // belonging to the other grouping simply matches nothing.
    pref: () => ({
      ...data.table.pref,
      collapsed: data.table.pref.collapsed ?? (byDue ? ["done"] : terminalKeys(data.statuses)),
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

  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";

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

  /**
   * The one filter that is set from outside the bar (the assignee picker inside it, and the
   * board's own controls). Through `filterUrl`, which drops the page along with the filter.
   */
  function setFilter(key: string, value: string) {
    void goto(filterUrl(page.url, key, value), { keepFocus: true, noScroll: true });
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

  /**
   * The board's filters, rendered by the shared bar (#354).
   *
   * This screen had the pattern first — the mobile collapse behind a counted toggle, the "wissen"
   * link, the search-then-pickers-then-chips order — hand-written, and the four other lists each
   * grew a partial copy of it. It is the bar's now, so the copies cannot drift again.
   *
   * The keys are the long ones (`company_id`, not `company`), unlike the register lists: the
   * dashboard tiles, the client hub and half the notification hrefs deep-link here, and renaming
   * a URL parameter breaks every link anyone has already sent.
   *
   * Two things the bar cannot express, and they say so rather than being drawn beside it. The
   * assignee is a `MemberPicker`, whose "everyone" is a sentinel rather than an empty value —
   * absent means *you* here. And a tenant's labels carry their own colour, which is the one
   * place a chip's colour is information rather than decoration.
   */
  const filterDefs: FilterDef<string>[] = $derived([
    { kind: "search", key: "q", placeholder: t("tasks.search_placeholder") },
    {
      kind: "select",
      key: "company_id",
      placeholder: t("tasks.field.company"),
      options: companyItems,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "select",
      key: "project_id",
      placeholder: t("tasks.field.project"),
      options: projectItems,
      archived: projectPicker.retired,
      archivedLabel: projectArchivedLabel(),
    },
    {
      kind: "custom",
      key: "assignee_user_id",
      hidden: isPortal,
      render: assigneeFilter,
      // Absent resolves to *you* server-side, so "is this narrowing the list" is not "is the
      // parameter present": it is "is this anyone other than the default".
      active: assigneeFilterValue !== "" && assigneeFilterValue !== (page.data.user?.id ?? ""),
    },
    {
      kind: "pills",
      key: "due",
      options: dueOptions.map((option) => ({ value: option, label: t(`tasks.due.${option}`) })),
    },
    // The dashboard tile's "no client or project" bucket arrives here as `?unlinked=1`; the chip
    // is what makes that a visible filter rather than a silently narrowed list.
    {
      kind: "pills",
      key: "unlinked",
      options: [{ value: "1", label: t("tasks.filter.unlinked") }],
    },
    // The abandoned create-then-edit rows (#350). Reachable, so they can be renamed or deleted;
    // without it they sit among real work with nothing to gather them by.
    { kind: "pills", key: "unnamed", options: [{ value: "1", label: t("tasks.filter.unnamed") }] },
    // The rows an instance carried into #392, where the deadline became required. Findable so
    // they can be dated — one at a time, or as a selection through the ✎ beside this list.
    { kind: "pills", key: "undated", options: [{ value: "1", label: t("tasks.filter.undated") }] },
    {
      kind: "pills",
      key: "label_id",
      hidden: data.labels.length === 0,
      options: data.labels.map((label) => ({
        value: label.id,
        label: label.name,
        class: labelChipClass(label.color),
      })),
    },
  ]);
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
  <!-- Create-then-edit (#230, docs/UX.md Principle 3): the button posts a placeholder row and
       the action redirects to its detail page in edit mode, where every field lives. No dialog
       in front of it — asking three of a task's twenty fields on the way to the page that edits
       all twenty is a form in front of a form. Beside it, the other way in (#382): a task spoken
       in one breath, reviewed whole. Not a menu item — this is a primary create path, not a
       variant of one — and on a phone it is the reachable pair the FAB rule asks for. -->
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
      <form method="POST" action="?/create" use:enhance={creating.wrap()}>
        <!-- The list's own filters ride along, so a task made while looking at one client lands
             on that client — the same carry-through the dictation sheet already does. -->
        {#if data.filters.company_id}
          <input type="hidden" name="company_id" value={data.filters.company_id} />
        {/if}
        {#if data.filters.project_id}
          <input type="hidden" name="project_id" value={data.filters.project_id} />
        {/if}
        <Button loading={creating.active}>{t("tasks.new")}</Button>
      </form>
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

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#snippet assigneeFilter()}
  <!-- A `MemberPicker`, not a `Combobox` over ids: it draws avatars and knows the deactivated
       from the current (`$lib/core/members`). The bar places it; the control stays ours. -->
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
{/snippet}

<FilterBar filters={filterDefs} idPrefix="task-filter">
  {#snippet actions()}
    <!-- What the board is grouped by (#395). Real links, because the grouping is in the URL and
         the URL is the view: a colleague pasting `?group=status` gets the board being looked at,
         and the back button undoes a switch. It sits among the list's own controls rather than
         among the filter chips, because it changes how the same rows *read* and not which rows
         there are — the same reason Kolommen lives here. -->
    <div
      class="flex items-center gap-1 text-xs"
      role="group"
      aria-label={t("tasks.group_by.label")}
    >
      <span class="hidden text-text-muted sm:inline">{t("tasks.group_by.label")}</span>
      {#each TASK_GROUPINGS as grouping (grouping)}
        <a
          href={groupHref(grouping)}
          data-sveltekit-noscroll
          aria-current={data.grouping === grouping ? "true" : undefined}
          class="rounded-full px-3 py-1 font-medium {data.grouping === grouping
            ? 'bg-brand text-white'
            : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
          >{t(`tasks.group_by.${grouping}`)}</a
        >
      {/each}
    </div>
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
    <!-- Reachable even when a filter empties the board: the sort that emptied it is cycled off
         from here. -->
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
    <!-- Absolute *and* relative (#395): "18 aug" alone asks the reader to know today's date and
         subtract, and "3 dagen te laat" alone cannot be matched against a calendar. Overdue work
         stays loudly red (docs/UX.md, principle 4); a finished task's date is history. -->
    <DueDate due={task.due_date} {today} muted={isDone(task)} />
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

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.tasks}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  {groups}
  groupBy={(task) => (byDue ? sectionOf(task) : task.status)}
  rowClass={(task) => priorityRailClass(task.priority, isDone(task))}
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
