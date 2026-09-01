<script lang="ts">
  /**
   * Shared task row: complete-toggle, title linking to the card, label chips, due date,
   * checklist progress, comment count, the faces of everyone on it.
   * Used by the tasks list (below `sm`), the project to-do list and the company panel.
   *
   * Since #395 it carries the urgency vocabulary the board is grouped by, and it carries it here
   * *because* it is shared: the team asked for the same treatment on the client and project
   * lists, and one component is how those get it without four copies of the rule.
   *  - the deadline prints its distance beside it (`DueDate`), so `18 aug` no longer asks the
   *    reader to know today's date and subtract;
   *  - a `high` priority draws a rail down the row's left edge (`priorityRailClass`), so the one
   *    task that cannot slip is found before the text is read.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { burnPct, burnTextClass } from "$lib/core/burn";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import { t } from "$lib/core/i18n";
  import { orgToday } from "$lib/core/today";
  import { taskBurn } from "$lib/modules/tasks/budget";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import DueDate from "$lib/modules/tasks/DueDate.svelte";
  import { priorityRailClass } from "$lib/modules/tasks/priority";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import {
    defaultStatusKey,
    type TaskStatusDef,
    terminalStatusKey,
  } from "$lib/modules/tasks/statuses";
  import { formatMinutes } from "$lib/modules/time/format";

  interface Label {
    id: string;
    name: string;
    color: string;
  }

  interface TaskLike {
    id: string;
    title: string;
    status: string;
    priority: string;
    due_date?: string | null;
    allocated_minutes?: number | null;
    // Only when the caller's load asked for `hours=true` and may read hours (#313).
    logged_minutes?: number | null;
    remaining_minutes?: number | null;
    assignee_user_id?: string | null;
    /** The client contact this is assigned to (#453) — the face the roster cannot draw. */
    assignee_contact_name?: string | null;
    /** The roster (#375), primary first. Optional: the compact dashboard shapes carry neither it
     *  nor the mirrored column, and those rows draw no face at all. */
    assignees?: { user_id: string; is_primary: boolean }[];
    labels?: Label[];
    checklist_done?: number;
    checklist_total?: number;
    comment_count?: number;
    company_id?: string | null;
    // The client anchor is two columns, not one: a task with no `company_id` still reaches its
    // *project's* client, which is what decides whether the visibility marker warns.
    project_id?: string | null;
    visible_to_client?: boolean;
  }

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
    avatar_url?: string | null;
  }

  let {
    task,
    toggleAction = "?/toggle",
    members = [],
    statuses = [],
    today = orgToday(),
  }: {
    task: TaskLike;
    toggleAction?: string;
    members?: Member[];
    /** The org's configured statuses (issue #62). Empty falls back to open/done behaviour. */
    statuses?: TaskStatusDef[];
    today?: string;
  } = $props();

  // The current status's definition, when the caller supplied the vocabulary. "Finished" is its
  // `is_terminal` flag; without a vocabulary we fall back to the literal "done" so callers that
  // don't load statuses (project detail, dashboard widgets) keep working.
  const statusDef = $derived(statuses.find((s) => s.key === task.status));
  const done = $derived(statusDef ? statusDef.is_terminal : task.status === "done");
  // The complete toggle moves to a terminal status and back to the default one.
  const toggleTo = $derived(
    statuses.length
      ? done
        ? defaultStatusKey(statuses)
        : terminalStatusKey(statuses)
      : done
        ? "open"
        : "done",
  );
  // A pill for a status that is neither the resting default nor a finished one (was: in_progress).
  const pill = $derived(
    statusDef && !statusDef.is_terminal && !statusDef.is_default ? statusDef : null,
  );
  // Every face on the task (#375), primary first — the row is narrow, so it draws up to three and
  // counts the rest. Falls back to the mirrored column for the compact shapes that carry no
  // roster, which is exactly the row this looked like before.
  const roster = $derived(
    task.assignees?.length
      ? task.assignees
      : task.assignee_user_id
        ? [{ user_id: task.assignee_user_id, is_primary: true }]
        : [],
  );
  const faces = $derived(
    roster
      .map((link) => ({ link, member: members.find((m) => m.user_id === link.user_id) }))
      .filter((entry) => entry.member !== undefined),
  );
  // `null` wherever the load did not ask for the burn (the project to-do list, the dashboard
  // widget) or the caller may not read hours — the pill then shows the plain allocation it
  // always did, rather than a zero that would read as "nothing logged".
  const burn = $derived(taskBurn(task));

  // The complete toggle is a task-status write (PATCH /api/v1/tasks/{id}), so it mirrors the API's
  // `tasks.task.write`. This row is shared across the tasks list, the project to-do and the company
  // panel — all of which a read-only portal client can reach (#244) — so it self-gates here rather
  // than trusting each caller to pass a flag: a viewer without the write sees a static marker.
  //
  // Per *row*, not per screen: `:own` means assignee, so the base-key check drew a live checkbox
  // on every colleague's task for the seeded `member` role, and ticking one 403'd.
  const canToggle = $derived(canWriteTask(page.data.user, task));
</script>

<!-- `flex-wrap` + a real flex-basis on the title block: with every badge `shrink-0`, a busy row
     on a phone used to squeeze the `flex-1 min-w-0` title to literally zero width — a task you
     could no longer read or open. Wrapping moves the badge cluster to its own line instead;
     on a desktop everything still fits on one. -->
<div
  class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 pl-3.5 pr-4 hover:bg-surface
    {priorityRailClass(task.priority, done)}"
>
  {#if canToggle}
    <form method="POST" action={toggleAction} use:enhance>
      <input type="hidden" name="id" value={task.id} />
      <input type="hidden" name="status" value={toggleTo} />
      <button
        class="flex h-5 w-5 items-center justify-center rounded border text-xs
          {done
          ? 'border-brand bg-brand text-white'
          : 'border-border text-transparent hover:border-brand'}"
        aria-label={t("tasks.toggle_done")}>✓</button
      >
    </form>
  {:else}
    <!-- Read-only viewer (portal client, #244): the status shows, the toggle does not. -->
    <span
      class="flex h-5 w-5 items-center justify-center rounded border text-xs
        {done ? 'border-brand bg-brand text-white' : 'border-border text-transparent'}"
      aria-label={t("tasks.toggle_done")}>✓</span
    >
  {/if}

  <div class="min-w-0 flex-1 basis-40">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <a
        href={`/tasks/${task.id}`}
        class="truncate text-sm font-medium {done
          ? 'text-text-muted line-through'
          : 'text-text hover:text-brand'}">{task.title}</a
      >
      <!-- Beside the title, not out in the badge cluster: "a client is reading this" is a fact
           about the task itself, and it has to survive the row wrapping on a phone. -->
      <ClientVisibilityIcon
        visible={task.visible_to_client ?? false}
        companyId={task.company_id}
        projectId={task.project_id}
        size={13}
      />
      {#each task.labels ?? [] as label (label.id)}
        <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
          >{label.name}</span
        >
      {/each}
      {#if pill}
        <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(pill.color)}"
          >{pill.name}</span
        >
      {:else if !statuses.length && task.status === "in_progress"}
        <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
          >{t("tasks.status.in_progress")}</span
        >
      {/if}
    </div>
  </div>

  <div class="flex shrink-0 items-center gap-2.5">
    {#if task.priority === "high" && !done}
      <span class="text-[11px] font-semibold uppercase text-red-500 dark:text-red-400"
        >{t("tasks.priority.high")}</span
      >
    {/if}
    {#if (task.checklist_total ?? 0) > 0}
      <span
        class="rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums
          {task.checklist_done === task.checklist_total
          ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
          : 'bg-surface text-text-muted'}">☑ {task.checklist_done}/{task.checklist_total}</span
      >
    {/if}
    {#if (task.comment_count ?? 0) > 0}
      <span class="text-[11px] tabular-nums text-text-muted">💬 {task.comment_count}</span>
    {/if}
    {#if burn}
      <!-- "⏱ 1u 30m / 3u" (#313). No bar: this pill is 20 px tall on a phone, so the state is
           carried by the figure and the one text colour that shouts — `burnTextClass` is red
           only over budget, which is exactly the state worth interrupting someone for. -->
      <span
        class="rounded bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums {burnTextClass(
          burnPct(burn.spent, burn.budget),
        )}"
        title={burn.remainingText}
      >
        ⏱ {burn.spentText}
      </span>
    {:else if task.allocated_minutes}
      <span
        class="rounded bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted"
      >
        ⏱ {formatMinutes(task.allocated_minutes)}
      </span>
    {/if}
    <!-- Absolute *and* relative (#395). A finished task's deadline is history, so it stays
         grey however late it was: red on a struck-through title is the loudest way to say
         something that no longer matters. -->
    <DueDate due={task.due_date} {today} muted={done} />
    {#each faces.slice(0, 3) as { link, member } (link.user_id)}
      <Avatar
        name={member?.full_name}
        email={member?.email ?? null}
        avatarUrl={member?.avatar_url ?? null}
        size="sm"
      />
    {/each}
    {#if faces.length > 3}
      <span
        class="text-xs text-text-muted"
        title={faces
          .slice(3)
          .map(({ member }) => member?.full_name || member?.email)
          .join(", ")}>+{faces.length - 3}</span
      >
    {/if}
    <!-- A task held by a client contact (#273) has no roster face; the person still gets one
         (#453), or "who is on this" reads as nobody on every board, panel and widget. -->
    {#if task.assignee_contact_name}
      <Avatar name={task.assignee_contact_name} email={null} avatarUrl={null} size="sm" />
    {/if}
  </div>
</div>
