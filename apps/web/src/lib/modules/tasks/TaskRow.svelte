<script lang="ts">
  /**
   * Shared task row: complete-toggle, title linking to the card, label chips, due date
   * (red when overdue), checklist progress, comment count, assignee initials.
   * Used by the tasks list, the project to-do list and the company panel.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
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
    assignee_user_id?: string | null;
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
    today = new Date().toISOString().slice(0, 10),
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
  const overdue = $derived(!done && !!task.due_date && task.due_date < today);
  const assignee = $derived(members.find((m) => m.user_id === task.assignee_user_id));

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
<div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 hover:bg-surface">
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
    {#if task.allocated_minutes}
      <span
        class="rounded bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted"
      >
        ⏱ {formatMinutes(task.allocated_minutes)}
      </span>
    {/if}
    {#if task.due_date}
      <span
        class="text-xs tabular-nums {overdue
          ? 'font-semibold text-red-600 dark:text-red-400'
          : 'text-text-muted'}"
      >
        {fmtDayMonth(task.due_date)}
      </span>
    {/if}
    {#if assignee}
      <Avatar
        name={assignee.full_name}
        email={assignee.email}
        avatarUrl={assignee.avatar_url ?? null}
        size="sm"
      />
    {/if}
  </div>
</div>
