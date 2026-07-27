<script lang="ts">
  /** Dashboard widget: open tasks grouped per project (fallback: per client). */
  import { t } from "$lib/core/i18n";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    title: string;
    status: string;
    due_date: string | null;
    project_id: string | null;
    company_id: string | null;
  }
  interface Named {
    id: string;
    name: string;
  }
  interface Payload {
    tasks: Row[];
    projects: Named[];
    companies: Named[];
  }

  const payload = $derived((data ?? { tasks: [], projects: [], companies: [] }) as Payload);
  const today = new Date().toISOString().slice(0, 10);

  interface Group {
    key: string;
    label: string;
    /** The project/company record the group is (issue #15 — rows link to their entity). */
    entityHref: string;
    /** The open-tasks list filtered to this group (the aggregate's own filtered list). */
    listHref: string;
    count: number;
    overdue: number;
  }

  const groups = $derived.by<Group[]>(() => {
    const open = payload.tasks.filter((task) => task.status !== "done");
    const byKey = new Map<string, Group>();
    for (const task of open) {
      let key: string, label: string, entityHref: string, listHref: string;
      if (task.project_id) {
        key = `p:${task.project_id}`;
        label = payload.projects.find((p) => p.id === task.project_id)?.name ?? "?";
        entityHref = `/projects/${task.project_id}`;
        // This widget's own count is org-wide (no assignee filter) — the tasks list defaults
        // its person switcher to "yourself", so override it to keep the count and the list in sync.
        listHref = `/tasks?project_id=${task.project_id}&assignee_user_id=${ALL_ASSIGNEES}`;
      } else if (task.company_id) {
        key = `c:${task.company_id}`;
        label = payload.companies.find((c) => c.id === task.company_id)?.name ?? "?";
        entityHref = `/companies/${task.company_id}`;
        listHref = `/tasks?company_id=${task.company_id}&assignee_user_id=${ALL_ASSIGNEES}`;
      } else {
        key = "none";
        label = t("time.general");
        entityHref = "/tasks";
        listHref = `/tasks?assignee_user_id=${ALL_ASSIGNEES}`;
      }
      const group = byKey.get(key) ?? { key, label, entityHref, listHref, count: 0, overdue: 0 };
      group.count += 1;
      if (task.due_date && task.due_date < today) group.overdue += 1;
      byKey.set(key, group);
    }
    return [...byKey.values()].sort((a, b) => b.count - a.count);
  });
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
      {#each groups as group (group.key)}
        <li class="flex items-center justify-between gap-2 py-2">
          <!-- The name is the record; the count is the filtered task list (issue #15). -->
          <a
            href={group.entityHref}
            class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
            >{group.label}</a
          >
          {#if group.overdue > 0}
            <span
              class="shrink-0 rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-red-700 dark:bg-red-950 dark:text-red-300"
            >
              {t("tasks.overdue_count", { count: group.overdue })}
            </span>
          {/if}
          <a
            href={group.listHref}
            class="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs font-semibold tabular-nums text-text-muted hover:text-brand"
          >
            {group.count}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>
