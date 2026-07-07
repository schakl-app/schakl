<script lang="ts">
  /** My Day widget: the current user's open tasks (CLAUDE.md §10). Read-only summary. */
  import { t } from "$lib/core/i18n";

  let { data }: { data: unknown } = $props();

  interface MyTask {
    id: string;
    title: string;
    priority: string;
    due_date: string | null;
  }
  const tasks = $derived((data ?? []) as MyTask[]);
</script>

<div class="rounded-xl border border-neutral-200 bg-white p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-neutral-900">{t("dashboard.my_day.tasks")}</h2>
    <a href="/tasks" class="text-xs text-brand hover:underline">{t("common.actions")}</a>
  </div>
  {#if tasks.length === 0}
    <p class="text-sm text-neutral-500">{t("dashboard.my_day.no_tasks")}</p>
  {:else}
    <ul class="divide-y divide-neutral-100">
      {#each tasks as task (task.id)}
        <li class="flex items-center justify-between py-2">
          <a href="/tasks" class="text-sm text-neutral-900 hover:text-brand">{task.title}</a>
          <span class="text-xs text-neutral-500">
            {#if task.due_date}{task.due_date}{:else}{t(`tasks.priority.${task.priority}`)}{/if}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</div>
