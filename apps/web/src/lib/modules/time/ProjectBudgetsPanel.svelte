<script lang="ts">
  /**
   * "How many hours are still available?" — answered on the screen where hours are logged.
   *
   * Time entries attach to a **project**, and a project covered by an active agreement burns
   * against that agreement's included hours (#225). So this is the one place the retainer's
   * remaining hours belong: the timesheet, next to the form that spends them — not a second
   * subscription picker that would track its own, disagreeing, total.
   *
   * Costs nothing extra: it renders the project lookup the time layout already loads with
   * `hours=true` (docs/PERFORMANCE.md — no 200-row fetch to show a handful). Hottest burn
   * first, the one scale from `core/burn.ts`, and it scrolls rather than truncating, so it
   * never reads as "those are all the budgets" when it isn't.
   */
  import { burnBarClass, burnBarWidth, burnPct, burnTextClass } from "$lib/core/burn";
  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  interface ProjectRow {
    id: string;
    name?: string;
    status?: string;
    company_id?: string | null;
    hours?: {
      budget_hours?: number | null;
      spent_hours?: number;
      remaining_hours?: number | null;
    } | null;
    budget_sources?: { subscription_id: string; name: string }[] | null;
  }

  let {
    projects,
    companies = [],
  }: {
    projects: ProjectRow[];
    /** For naming the client per row; the time layout already holds these. */
    companies?: { id: string; name?: string }[];
  } = $props();

  const companyName = $derived.by(() => {
    const byId = new Map(companies.map((c) => [c.id, c.name ?? ""]));
    return (id?: string | null) => (id ? (byId.get(id) ?? "") : "");
  });

  const rows = $derived.by(() => {
    const items = projects
      .filter((p) => p.status !== "archived" && p.hours?.budget_hours != null)
      .map((p) => {
        const spent = p.hours?.spent_hours ?? 0;
        const budget = p.hours?.budget_hours ?? 0;
        return {
          id: p.id,
          name: p.name ?? "",
          company: companyName(p.company_id),
          spent,
          budget,
          remaining: p.hours?.remaining_hours ?? Math.round((budget - spent) * 100) / 100,
          pct: burnPct(spent, budget),
          sources: (p.budget_sources ?? []).map((s) => s.name).join(", "),
        };
      });
    items.sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0));
    return items;
  });
</script>

{#if rows.length > 0}
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-sm font-semibold text-text">{t("time.budget.title")}</h2>
    <ul class="max-h-80 space-y-3 overflow-y-auto">
      {#each rows as project (project.id)}
        <li>
          <div class="flex items-baseline justify-between gap-2">
            <a
              href={`/projects/${project.id}`}
              class="min-w-0 truncate text-sm font-medium text-text hover:text-brand"
            >
              {project.name}
            </a>
            <span class="shrink-0 text-sm font-semibold tabular-nums {burnTextClass(project.pct)}">
              {project.remaining < 0
                ? t("time.budget.over", { hours: fmtNumber(-project.remaining, 1) })
                : t("time.budget.remaining", { hours: fmtNumber(project.remaining, 1) })}
            </span>
          </div>
          {#if project.pct != null}
            <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-surface">
              <!-- Clamp the bar, never the number (docs/UX.md). -->
              <div
                class="h-full rounded-full {burnBarClass(project.pct)}"
                style="width: {burnBarWidth(project.pct)}%"
              ></div>
            </div>
          {/if}
          <div class="mt-1 flex flex-wrap items-baseline justify-between gap-x-2 text-xs">
            <span class="tabular-nums text-text-muted">
              {t("time.budget.spent", {
                spent: fmtNumber(project.spent, 1),
                budget: fmtNumber(project.budget, 1),
              })}
            </span>
            <span class="min-w-0 truncate text-text-muted">
              {project.sources
                ? t("time.budget.from_subscription", { name: project.sources })
                : project.company}
            </span>
          </div>
        </li>
      {/each}
    </ul>
  </section>
{/if}
