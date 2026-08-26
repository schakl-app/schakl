<script lang="ts">
  /**
   * Every budgeted project's burn in one chart (#437). The sibling bar-list tile shows the
   * hottest four; this is the "how are all budgets doing" picture the team asked for — a
   * slice per project, coloured by burn *state* (ok/soon/late, `core/burn` — never the
   * tenant's brand, #404) so an over-budget slice is visibly a claim, and an honest "overig"
   * bucket carrying the tail's hours and naming its size (§17).
   *
   * Nothing on it is a dead end (#15): a slice opens its project, a figure opens the report
   * rows behind it, and the over-budget aggregate opens `/projects?burn=over` — the filter
   * that exists so this number has a list to agree with. The aggregate strip is also the
   * palette's "never colour alone" half for the slices: the same fact with a glyph and words.
   */
  import { burnPct, burnState } from "$lib/core/burn";
  import { fmtNumber } from "$lib/core/format";
  import type { HoursFields } from "$lib/core/hours";
  import { t, tn } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import DonutChart from "$lib/core/ui/charts/DonutChart.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";

  let { data }: { data: unknown } = $props();

  interface ProjectRow {
    id: string;
    name: string;
    company_name?: string | null;
    hours?: HoursFields | null;
  }
  const payload = $derived(
    (data ?? {}) as {
      items?: ProjectRow[];
      total?: number;
      tail_spent_hours?: number;
      tail_budget_hours?: number;
      over_budget?: number;
    },
  );
  const items = $derived(payload.items ?? []);
  const total = $derived(payload.total ?? items.length);
  const tailCount = $derived(Math.max(0, total - items.length));

  const slices = $derived(
    items.map((p) => ({
      label: p.company_name ? `${p.name} · ${p.company_name}` : p.name,
      value: p.hours?.spent_hours ?? 0,
      state: burnState(burnPct(p.hours?.spent_hours ?? 0, p.hours?.budget_hours ?? null)),
      href: `/projects/${p.id}`,
      valueHref: `/overview?project_id=${p.id}`,
    })),
  );

  const formatHours = (value: number) => t("hours.spent", { hours: fmtNumber(value) });
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.projects.budgets_donut")}
  href="/projects"
  linkLabel={t("nav.projects")}
>
  {#if slices.length === 0}
    <p class="text-sm text-text-muted">{t("projects.widget.no_budgets")}</p>
  {:else}
    <DonutChart
      {slices}
      otherLabel={tn("projects.widget.donut_other", tailCount)}
      otherValue={payload.tail_spent_hours ?? 0}
      centerLabel={t("projects.widget.donut_center")}
      format={formatHours}
    />
    {#if (payload.over_budget ?? 0) > 0}
      <p class="mt-3">
        <a href="/projects?burn=over" class="inline-flex hover:underline">
          <StateMark
            state="late"
            variant="chip"
            label={tn("projects.widget.over_budget", payload.over_budget ?? 0)}
          />
        </a>
      </p>
    {/if}
  {/if}
</DashboardWidgetCard>
