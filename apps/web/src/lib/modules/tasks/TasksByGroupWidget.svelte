<script lang="ts">
  /** Dashboard widget: open tasks grouped per project (fallback: per client). */
  import { t } from "$lib/core/i18n";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";

  let { data }: { data: unknown } = $props();

  interface Group {
    entity_type: string;
    entity_id: string | null;
    label: string;
    count: number;
    overdue: number;
  }
  const groups = $derived((data ?? []) as Group[]);
  const entityHref = (group: Group) =>
    group.entity_type === "project"
      ? `/projects/${group.entity_id}`
      : group.entity_type === "company"
        ? `/companies/${group.entity_id}`
        : "/tasks";
  const listHref = (group: Group) => {
    const filter =
      group.entity_type === "project"
        ? `project_id=${group.entity_id}&`
        : group.entity_type === "company"
          ? `company_id=${group.entity_id}&`
          : "";
    return `/tasks?${filter}assignee_user_id=${ALL_ASSIGNEES}`;
  };
</script>

<div class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("dashboard.open_by_group.title")}</h2>
    <a href="/tasks" class="text-xs text-brand hover:underline">{t("common.actions")}</a>
  </div>
  {#if groups.length === 0}
    <p class="text-sm text-text-muted">{t("dashboard.open_by_group.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each groups as group (`${group.entity_type}:${group.entity_id}`)}
        <li class="flex items-center justify-between gap-2 py-2">
          <!-- The name is the record; the count is the filtered task list (issue #15). -->
          <a
            href={entityHref(group)}
            class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
            >{group.label ?? t("time.general")}</a
          >
          {#if group.overdue > 0}
            <span
              class="shrink-0 rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-red-700 dark:bg-red-950 dark:text-red-300"
            >
              {t("tasks.overdue_count", { count: group.overdue })}
            </span>
          {/if}
          <a
            href={listHref(group)}
            class="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs font-semibold tabular-nums text-text-muted hover:text-brand"
          >
            {group.count}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>
