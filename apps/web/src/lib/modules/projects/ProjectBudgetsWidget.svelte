<script lang="ts">
  /** My Day widget: the budgeted projects burning hottest — the one burn scale (core/burn),
   *  unclamped number, clamped bar, loudly red over budget (UX Principle 4). */
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface ProjectRow {
    id: string;
    name: string;
    hours?: {
      budget_hours?: number | null;
      spent_hours?: number;
    } | null;
  }
  // `/projects/dashboard-budgets` returns the budgeted projects already sorted by burn and cut
  // to the tile's length (#290), so there is nothing left to filter, sort or slice here.
  const rows = $derived(
    ((data ?? []) as ProjectRow[]).map((p) => ({
      id: p.id,
      name: p.name,
      spent: p.hours?.spent_hours ?? 0,
      budget: p.hours?.budget_hours ?? 0,
      pct: burnPct(p.hours?.spent_hours ?? 0, p.hours?.budget_hours ?? null),
    })),
  );
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.projects.budgets")}
  href="/projects"
  linkLabel={t("nav.projects")}
>
  {#if rows.length === 0}
    <p class="text-sm text-text-muted">{t("projects.widget.no_budgets")}</p>
  {:else}
    <ul class="space-y-3">
      {#each rows as project (project.id)}
        <li>
          <div class="flex items-center justify-between gap-2 text-sm">
            <a
              href={`/projects/${project.id}`}
              class="min-w-0 truncate font-medium text-text hover:text-brand"
            >
              {project.name}
            </a>
            <span
              class="shrink-0 tabular-nums {project.pct != null && project.pct >= 100
                ? 'font-medium text-red-600 dark:text-red-400'
                : 'text-text-muted'}"
            >
              {t("projects.widget.spent", {
                spent: fmtNumber(project.spent, 1),
                budget: fmtNumber(project.budget, 1),
              })}
            </span>
          </div>
          {#if project.pct != null}
            <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-surface">
              <div
                class="h-full rounded-full {burnBarClass(project.pct)}"
                style="width: {burnBarWidth(project.pct)}%"
              ></div>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</DashboardWidgetCard>
