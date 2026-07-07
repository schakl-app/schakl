<script lang="ts">
  /** Company-detail panel: open tasks attached to this company (CLAUDE.md §6). */
  import { t } from "$lib/core/i18n";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelTask {
    id: string;
    title: string;
    status: string;
    priority: string;
    due_date: string | null;
  }
  const tasks = $derived((data.tasks ?? []) as PanelTask[]);
</script>

{#if tasks.length === 0}
  <p class="text-sm text-neutral-500">{t("tasks.empty")}</p>
{:else}
  <ul class="divide-y divide-neutral-100">
    {#each tasks as task (task.id)}
      <li class="flex items-center justify-between py-2">
        <a href="/tasks" class="text-sm font-medium text-neutral-900 hover:text-brand">{task.title}</a>
        <span class="text-xs text-neutral-500">
          {#if task.due_date}{task.due_date}{/if}
          · {t(`tasks.status.${task.status}`)}
        </span>
      </li>
    {/each}
  </ul>
{/if}
