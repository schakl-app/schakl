<script lang="ts">
  /**
   * Shared task row: complete-toggle, title linking to the card, label chips, due date
   * (red when overdue), checklist progress, comment count, assignee initials.
   * Used by the tasks list, the project to-do list and the company panel.
   */
  import { enhance } from "$app/forms";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
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
  }

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string;
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
  const pill = $derived(statusDef && !statusDef.is_terminal && !statusDef.is_default ? statusDef : null);
  const overdue = $derived(!done && !!task.due_date && task.due_date < today);
  const assignee = $derived(members.find((m) => m.user_id === task.assignee_user_id));

  function initials(member: Member): string {
    const source = member.full_name || member.email;
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
  }
</script>

<div class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface">
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

  <div class="min-w-0 flex-1">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <a
        href={`/tasks/${task.id}`}
        class="truncate text-sm font-medium {done
          ? 'text-text-muted line-through'
          : 'text-text hover:text-brand'}">{task.title}</a
      >
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

  {#if task.priority === "high" && !done}
    <span class="shrink-0 text-[11px] font-semibold uppercase text-red-500 dark:text-red-400"
      >{t("tasks.priority.high")}</span
    >
  {/if}
  {#if (task.checklist_total ?? 0) > 0}
    <span
      class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums
        {task.checklist_done === task.checklist_total
        ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
        : 'bg-surface text-text-muted'}">☑ {task.checklist_done}/{task.checklist_total}</span
    >
  {/if}
  {#if (task.comment_count ?? 0) > 0}
    <span class="shrink-0 text-[11px] tabular-nums text-text-muted">💬 {task.comment_count}</span>
  {/if}
  {#if task.allocated_minutes}
    <span
      class="shrink-0 rounded bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted"
    >
      ⏱ {formatMinutes(task.allocated_minutes)}
    </span>
  {/if}
  {#if task.due_date}
    <span
      class="shrink-0 text-xs tabular-nums {overdue
        ? 'font-semibold text-red-600 dark:text-red-400'
        : 'text-text-muted'}"
    >
      {fmtDayMonth(task.due_date)}
    </span>
  {/if}
  {#if assignee}
    <span
      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10 text-[10px] font-semibold text-brand"
      title={assignee.full_name || assignee.email}>{initials(assignee)}</span
    >
  {/if}
</div>
