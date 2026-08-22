<script lang="ts">
  /** My Day widget: the budgeted projects burning hottest — the one burn scale (core/burn),
   *  unclamped number, clamped bar, loudly red over budget (UX Principle 4). The figure is
   *  spent-of-budget and says so in the same words as every other surface (core/hours, #340). */
  import { burnBarClass, burnBarWidth, burnPct, burnTextClass } from "$lib/core/burn";
  import { hoursBurn, type HoursFields } from "$lib/core/hours";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface ProjectRow {
    id: string;
    name: string;
    company_name?: string | null;
    hours?: HoursFields | null;
  }
  // `/projects/dashboard-budgets` returns the budgeted projects already sorted by burn and cut
  // to the tile's length (#290), so there is nothing left to filter, sort or slice here.
  const rows = $derived(
    ((data ?? []) as ProjectRow[]).map((p) => ({
      id: p.id,
      name: p.name,
      companyName: p.company_name,
      burn: hoursBurn(p.hours),
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
            <a href={`/projects/${project.id}`} class="group min-w-0 flex-1">
              <span class="block truncate font-medium text-text group-hover:text-brand">
                {project.name}
              </span>
              <!-- Whose budget this is. "Onderhoud" is four indistinguishable rows on a tile
                   spanning four clients, and only opening one told them apart (MyTasksWidget's
                   fix, same reason). -->
              {#if project.companyName}
                <span class="block truncate text-xs text-text-muted">{project.companyName}</span>
              {/if}
            </a>
            <!-- The burn is a total of time entries, so it opens the report those entries are
                 in — filtered to this project (issue #15). Spent of budget, with what is left on
                 hover in words: the tile and the lists answer the same question with the same
                 sentence (#340). -->
            <a
              href="/overview?project_id={project.id}"
              title={project.burn?.title}
              class="shrink-0 tabular-nums hover:underline {project.pct != null &&
              project.pct >= 100
                ? `font-medium ${burnTextClass(project.pct)}`
                : 'text-text-muted'}"
            >
              {project.burn?.spentText}
            </a>
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
