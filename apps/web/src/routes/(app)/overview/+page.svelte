<script lang="ts">
  /**
   * Overzicht — the year at a glance. Six vital signs, the month-by-month revenue against the
   * year before, and three rankings (clients, budgets, team), each a window onto the tab that
   * explains it. Nothing here is drawn twice: a tile, a bar and a heading each open the rows
   * they summarise (docs/UX.md Principle 7), and the tabs hold the detail.
   *
   * Revenue is the ledger's where the invoicing module is on and this manager may read the
   * agency's turnover; otherwise it is what the billable hours were worth, and the page says so
   * in one line rather than drawing a smaller figure under the same heading.
   */
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
  import { delta, sharePct } from "$lib/core/delta";
  import { fmtMoney, fmtNumber } from "$lib/core/format";
  import { hoursBurn } from "$lib/core/hours";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { pageTitle } from "$lib/core/title";
  import Card from "$lib/core/ui/Card.svelte";
  import MonthlyComparisonChart from "$lib/core/ui/charts/MonthlyComparisonChart.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import RankList, { type RankRow } from "$lib/core/ui/RankList.svelte";
  import Spinner from "$lib/core/ui/Spinner.svelte";
  import SummaryStrip from "$lib/core/ui/SummaryStrip.svelte";
  import YearStepper from "$lib/core/ui/YearStepper.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import type { ComponentProps } from "svelte";

  type SummaryTile = ComponentProps<typeof SummaryStrip>["tiles"][number];

  let { data } = $props();

  // Streamed behind the shell; a stale resolution loses (docs/PERFORMANCE.md) — two quick
  // presses on the year stepper leave two loads in flight, and only the latest may land.
  let payload = $state<Awaited<typeof data.payload> | null>(null);
  $effect(() => {
    const pending = data.payload;
    payload = null;
    void pending.then((resolved) => {
      if (data.payload === pending) payload = resolved;
    });
  });

  const year = $derived(data.year);
  const previousYear = $derived(data.year - 1);
  const hoursHref = $derived(`/overview/hours?date_from=${data.yearFrom}&date_to=${data.yearTo}`);
  const revenueHref = $derived(`/overview/revenue?year=${data.year}`);
  const employeesHref = $derived(
    `/overview/employees?date_from=${data.yearFrom}&date_to=${data.yearTo}`,
  );

  const memberName = (id: string) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? memberLabel(m) : "";
  };
  const companyName = (id?: string | null) =>
    payload?.companies.find((c) => c.id === id)?.name ?? t("time.general");

  /** One hint line under a figure: "+12% t.o.v. 2025", or why there is nothing to compare. */
  function versusHint(current: number, previous: number | null | undefined) {
    const d = delta(current, previous);
    return d
      ? {
          tone: d.tone,
          hint_key: "overview.hint.vs_previous",
          hint_params: { delta: d.text, year: previousYear },
        }
      : { hint_key: "overview.hint.no_previous", hint_params: { year: previousYear } };
  }

  const tiles = $derived.by((): SummaryTile[] => {
    if (!payload) return [];
    const out: SummaryTile[] = [];
    const invoiced = payload.invoiced;
    const hoursValue = payload.hoursValue;
    if (invoiced) {
      out.push({
        key: "invoiced_excl",
        label_key: "overview.tile.invoiced_excl",
        value: String(invoiced.total_excl),
        format: "money",
        href: revenueHref,
        ...versusHint(invoiced.total_excl, invoiced.previous_excl),
      });
      out.push({
        key: "invoiced_incl",
        label_key: "overview.tile.invoiced_incl",
        value: String(invoiced.total_incl),
        format: "money",
        href: `${revenueHref}&vat=incl`,
        hint_key: "overview.hint.vat",
        hint_params: { amount: fmtMoney(invoiced.total_tax) },
      });
    }
    if (payload.summary) {
      const s = payload.summary;
      out.push({
        key: "outstanding",
        label_key: "overview.tile.outstanding",
        value: String(s.open_total),
        format: "money",
        tone: s.overdue_count > 0 ? "warn" : "neutral",
        hint_key: s.overdue_count > 0 ? "overview.hint.overdue" : "overview.hint.nothing_overdue",
        hint_params: { count: s.overdue_count, amount: fmtMoney(s.overdue_total) },
        href: "/invoices?status=open",
      });
    }
    if (payload.team) {
      const minutes = payload.team.rows.reduce((sum, r) => sum + r.minutes, 0);
      const billable = payload.team.rows.reduce((sum, r) => sum + r.billable_minutes, 0);
      out.push({
        key: "hours",
        label_key: "overview.tile.hours",
        value: String(minutes / 60),
        format: "hours",
        href: hoursHref,
        hint_key: "overview.hint.billable",
        hint_params: { pct: sharePct(billable, minutes) },
      });
    }
    // Nothing is a number (SummaryStrip): an org that has never set an hourly rate has no
    // "value of hours" to report, and a € 0 tile over two empty years would read as a verdict.
    if (hoursValue && (hoursValue.total_current > 0 || hoursValue.total_previous > 0)) {
      out.push({
        key: "hours_value",
        label_key: "overview.tile.hours_value",
        value: String(hoursValue.total_current),
        format: "money",
        href: `${revenueHref}#hours-value`,
        ...versusHint(hoursValue.total_current, hoursValue.total_previous),
      });
    }
    if (payload.budgets) {
      const b = payload.budgets;
      out.push({
        key: "budgets_over",
        label_key: "overview.tile.budgets_over",
        value: String(b.over_budget),
        format: "number",
        tone: b.over_budget > 0 ? "bad" : b.total > 0 ? "good" : "neutral",
        hint_key: "overview.hint.budgets",
        hint_params: { count: b.total },
        href: "/projects?burn=over&status=active",
      });
    }
    return out;
  });

  // The month series: the ledger's, or the hours' worth where there is no ledger.
  const monthly = $derived.by(() => {
    if (!payload) return null;
    if (payload.invoiced) {
      return {
        current: payload.invoiced.months_excl,
        previous: payload.invoiced.months_previous_excl,
        titleKey: "overview.home.monthly",
      };
    }
    if (payload.hoursValue) {
      return {
        current: payload.hoursValue.months_current,
        previous: payload.hoursValue.months_previous,
        titleKey: "overview.home.monthly_hours_value",
      };
    }
    return null;
  });

  const clientRows = $derived.by((): RankRow[] => {
    if (!payload) return [];
    if (payload.invoiced) {
      const total = payload.invoiced.total_excl;
      return payload.invoiced.top_clients
        .filter((c) => c.excl > 0)
        .slice(0, 5)
        .map((c) => ({
          key: c.company_id,
          label: c.name,
          value: c.excl,
          valueText: fmtMoney(c.excl),
          badge: { text: t("overview.home.share", { pct: sharePct(c.excl, total) }) },
          href: `/companies/${c.company_id}`,
        }));
    }
    if (payload.hoursValue) {
      const total = payload.hoursValue.total_current;
      return payload.hoursValue.top_clients
        .filter((c) => c.revenue > 0)
        .slice(0, 5)
        .map((c) => ({
          key: c.company_id ?? "general",
          label: companyName(c.company_id),
          value: c.revenue,
          valueText: fmtMoney(c.revenue),
          badge: { text: t("overview.home.share", { pct: sharePct(c.revenue, total) }) },
          href: c.company_id ? `/companies/${c.company_id}` : null,
        }));
    }
    return [];
  });

  const teamRows = $derived.by((): RankRow[] => {
    if (!payload?.team) return [];
    return [...payload.team.rows]
      .sort((a, b) => b.minutes - a.minutes)
      .slice(0, 5)
      .map((r) => ({
        key: r.user_id,
        label: memberName(r.user_id),
        value: r.minutes,
        valueText: formatMinutes(r.minutes),
        meta: t("overview.hint.billable", { pct: sharePct(r.billable_minutes, r.minutes) }),
        href: `${hoursHref}&user_id=${r.user_id}`,
      }));
  });

  const budgetRows = $derived(payload?.budgets?.items ?? []);
</script>

<svelte:head>
  <title>{pageTitle(t("overview.home.title"))}</title>
</svelte:head>

<PageHeader title={t("overview.home.title")}>
  {#snippet subtitle()}{t("overview.home.subtitle", { year })}{/snippet}
  {#snippet actions()}
    <YearStepper {year} hrefFor={(y) => `?year=${y}`} />
  {/snippet}
</PageHeader>

{#if !payload}
  <div class="flex items-center gap-2 py-8 text-sm text-text-muted">
    <Spinner /><span>{t("common.loading")}</span>
  </div>
{:else}
  {#if !data.invoicing}
    <p class="mb-4 text-sm text-text-muted">{t("overview.home.no_invoicing")}</p>
  {/if}

  <SummaryStrip {tiles} />

  {#if monthly}
    <Card
      kind="panel"
      title={t(monthly.titleKey)}
      href={revenueHref}
      linkLabel={t("overview.home.link.revenue")}
      class="mb-4"
    >
      <MonthlyComparisonChart
        current={monthly.current}
        previous={monthly.previous}
        currentLabel={String(year)}
        previousLabel={String(previousYear)}
      />
    </Card>
  {/if}

  <div class="grid gap-4 lg:grid-cols-3">
    <Card
      kind="panel"
      title={t("overview.home.top_clients")}
      href={revenueHref}
      linkLabel={t("overview.home.link.revenue")}
    >
      <RankList rows={clientRows} emptyText={t("overview.home.empty_clients", { year })} />
    </Card>

    {#if data.projects}
      <Card
        kind="panel"
        title={t("overview.home.budgets")}
        href="/overview/projects"
        linkLabel={t("overview.home.link.projects")}
      >
        {#if budgetRows.length === 0}
          <p class="text-sm text-text-muted">{t("overview.home.empty_budgets")}</p>
        {:else}
          <ol class="space-y-2.5">
            {#each budgetRows as project (project.id)}
              {@const burn = hoursBurn(project.hours)}
              {@const pct = burnPct(project.hours.spent_hours, project.hours.budget_hours)}
              <li>
                <div class="mb-1 flex items-baseline justify-between gap-3">
                  <span class="min-w-0 truncate">
                    <a
                      href={`/projects/${project.id}`}
                      class="text-sm font-medium text-text hover:text-brand">{project.name}</a
                    >
                    {#if project.company_name}
                      <span class="ml-2 text-xs text-text-muted">{project.company_name}</span>
                    {/if}
                  </span>
                  <span
                    class="shrink-0 text-sm font-semibold tabular-nums text-text"
                    title={burn?.title}>{burn?.spentText ?? ""}</span
                  >
                </div>
                <div class="h-1.5 overflow-hidden rounded-full bg-surface">
                  <div
                    class="h-full rounded-full {burnBarClass(pct)}"
                    style="width:{burnBarWidth(pct)}%"
                  ></div>
                </div>
              </li>
            {/each}
          </ol>
          {#if payload.budgets && payload.budgets.total > budgetRows.length}
            <p class="mt-3 text-xs text-text-muted">
              {t("overview.home.budgets_more", {
                shown: budgetRows.length,
                total: payload.budgets.total,
              })}
            </p>
          {/if}
        {/if}
      </Card>
    {/if}

    <Card
      kind="panel"
      title={t("overview.home.team")}
      href={employeesHref}
      linkLabel={t("overview.home.link.employees")}
    >
      <RankList rows={teamRows} emptyText={t("overview.home.empty_team", { year })} />
      {#if payload.team && payload.team.rows.length > teamRows.length}
        <p class="mt-3 text-xs text-text-muted">
          {t("overview.home.team_more", {
            shown: teamRows.length,
            total: payload.team.rows.length,
            hours: fmtNumber(payload.team.rows.reduce((sum, r) => sum + r.minutes, 0) / 60, 1),
          })}
        </p>
      {/if}
    </Card>
  </div>
{/if}
