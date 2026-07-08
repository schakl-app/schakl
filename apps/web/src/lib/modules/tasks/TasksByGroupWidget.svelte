<script lang="ts">
  /** Dashboard widget: open tasks grouped per project (fallback: per client). */
  import { t } from "$lib/core/i18n";

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
    href: string;
    count: number;
    overdue: number;
  }

  const groups = $derived.by<Group[]>(() => {
    const open = payload.tasks.filter((task) => task.status !== "done");
    const byKey = new Map<string, Group>();
    for (const task of open) {
      let key: string, label: string, href: string;
      if (task.project_id) {
        key = `p:${task.project_id}`;
        label = payload.projects.find((p) => p.id === task.project_id)?.name ?? "?";
        href = `/tasks?project_id=${task.project_id}`;
      } else if (task.company_id) {
        key = `c:${task.company_id}`;
        label = payload.companies.find((c) => c.id === task.company_id)?.name ?? "?";
        href = `/tasks?company_id=${task.company_id}`;
      } else {
        key = "none";
        label = t("time.general");
        href = "/tasks";
      }
      const group = byKey.get(key) ?? { key, label, href, count: 0, overdue: 0 };
      group.count += 1;
      if (task.due_date && task.due_date < today) group.overdue += 1;
      byKey.set(key, group);
    }
    return [...byKey.values()].sort((a, b) => b.count - a.count);
  });
</script>

<div class="rounded-xl border border-neutral-200 bg-white p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-neutral-900">{t("dashboard.open_by_group.title")}</h2>
    <a href="/tasks" class="text-xs text-brand hover:underline">{t("common.actions")}</a>
  </div>
  {#if groups.length === 0}
    <p class="text-sm text-neutral-500">{t("dashboard.open_by_group.empty")}</p>
  {:else}
    <ul class="divide-y divide-neutral-100">
      {#each groups as group (group.key)}
        <li>
          <a
            href={group.href}
            class="flex items-center justify-between gap-2 py-2 hover:text-brand"
          >
            <span class="min-w-0 flex-1 truncate text-sm font-medium text-neutral-900"
              >{group.label}</span
            >
            {#if group.overdue > 0}
              <span
                class="shrink-0 rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-red-700"
              >
                {t("tasks.overdue_count", { count: group.overdue })}
              </span>
            {/if}
            <span
              class="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-neutral-600"
            >
              {group.count}
            </span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>
