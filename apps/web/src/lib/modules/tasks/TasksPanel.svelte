<script lang="ts">
  /** Company-detail panel: the client's task overview (CLAUDE.md §6). */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { labelChipClass } from "$lib/modules/tasks/labels";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelLabel {
    id: string;
    name: string;
    color: string;
  }
  interface PanelTask {
    id: string;
    title: string;
    status: string;
    priority: string;
    due_date: string | null;
    labels?: PanelLabel[];
    checklist_done?: number;
    checklist_total?: number;
    comment_count?: number;
  }
  const tasks = $derived((data.tasks ?? []) as PanelTask[]);
  const today = new Date().toISOString().slice(0, 10);
</script>

{#if tasks.length === 0}
  <p class="text-sm text-text-muted">{t("tasks.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each tasks as task (task.id)}
      {@const overdue = task.due_date != null && task.due_date < today}
      <li class="flex items-center gap-2 py-2">
        <a
          href={`/tasks/${task.id}`}
          class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
        >
          {task.title}
        </a>
        {#each task.labels ?? [] as label (label.id)}
          <span
            class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
            >{label.name}</span
          >
        {/each}
        {#if (task.checklist_total ?? 0) > 0}
          <span class="text-[11px] tabular-nums text-text-muted"
            >☑ {task.checklist_done}/{task.checklist_total}</span
          >
        {/if}
        {#if task.status === "in_progress"}
          <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
            >{t("tasks.status.in_progress")}</span
          >
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
      </li>
    {/each}
  </ul>
{/if}
<div class="mt-3 flex items-center gap-4">
  <!-- The tasks list defaults its person switcher to "yourself" — override it here so "view all"
       still means every assignee on this company, matching this panel's own unfiltered list. -->
  <a
    href={`/tasks?company_id=${companyId}&assignee_user_id=${ALL_ASSIGNEES}`}
    class="text-xs text-brand hover:underline"
  >
    {t("tasks.panel.view_all")}
  </a>
  {#if can(page.data.user, "tasks.task.create")}
    <!-- Quick-create from the client page (#230): a POST — never a link, which would create on
         hover-preload — that makes a minimal task pre-linked to this client, then lands on its
         detail page in edit mode. -->
    <form method="POST" action="/tasks?/create" use:enhance>
      <input type="hidden" name="company_id" value={companyId} />
      <button class="text-xs text-brand hover:underline">＋ {t("tasks.new")}</button>
    </form>
  {/if}
</div>
