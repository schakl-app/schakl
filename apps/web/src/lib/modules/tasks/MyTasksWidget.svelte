<script lang="ts">
  /** My Day widget: overdue / due-today / upcoming partitions of my open tasks. */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  let { data }: { data: unknown } = $props();

  interface MyTask {
    id: string;
    title: string;
    priority: string;
    due_date: string | null;
  }
  const tasks = $derived((data ?? []) as MyTask[]);
  const today = new Date().toISOString().slice(0, 10);

  const overdue = $derived(tasks.filter((task) => task.due_date != null && task.due_date < today));
  const dueToday = $derived(tasks.filter((task) => task.due_date === today));
  const upcoming = $derived(
    tasks.filter((task) => task.due_date == null || task.due_date > today),
  );
</script>

{#snippet taskList(rows: MyTask[], red: boolean)}
  <ul class="divide-y divide-neutral-100">
    {#each rows as task (task.id)}
      <li class="flex items-center justify-between gap-2 py-1.5">
        <a href={`/tasks/${task.id}`} class="min-w-0 flex-1 truncate text-sm text-neutral-900 hover:text-brand">
          {task.title}
        </a>
        <span class="shrink-0 text-xs tabular-nums {red ? 'font-semibold text-red-600' : 'text-neutral-500'}">
          {#if task.due_date}
            {fmtDayMonth(task.due_date)}
          {:else}
            {t(`tasks.priority.${task.priority}`)}
          {/if}
        </span>
      </li>
    {/each}
  </ul>
{/snippet}

<div class="rounded-xl border border-neutral-200 bg-white p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-neutral-900">{t("dashboard.my_day.tasks")}</h2>
    <a href="/tasks" class="text-xs text-brand hover:underline">{t("common.actions")}</a>
  </div>

  {#if tasks.length === 0}
    <p class="text-sm text-neutral-500">{t("dashboard.my_day.no_tasks")}</p>
  {:else}
    {#if overdue.length > 0}
      <h3 class="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-red-600">
        {t("dashboard.my_day.overdue")}
        <span class="rounded-full bg-red-100 px-1.5 text-[10px] tabular-nums">{overdue.length}</span>
      </h3>
      {@render taskList(overdue, true)}
    {/if}
    {#if dueToday.length > 0}
      <h3 class="mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-brand">
        {t("dashboard.my_day.due_today")}
      </h3>
      {@render taskList(dueToday, false)}
    {/if}
    {#if upcoming.length > 0}
      <h3 class="mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-400">
        {t("dashboard.my_day.upcoming")}
      </h3>
      {@render taskList(upcoming, false)}
    {/if}
  {/if}
</div>
