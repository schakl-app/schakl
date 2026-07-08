<script lang="ts">
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DonutChart from "$lib/core/ui/charts/DonutChart.svelte";
  import MonthlyComparisonChart from "$lib/core/ui/charts/MonthlyComparisonChart.svelte";

  let { data } = $props();

  const stats = $derived(data.stats);
  const companyName = (id?: string | null) =>
    data.companies.find((c) => c.id === id)?.name ?? t("time.general");

  const delta = $derived.by(() => {
    if (!stats || stats.total_previous <= 0) return null;
    return Math.round(((stats.total_current - stats.total_previous) / stats.total_previous) * 100);
  });

  const slices = $derived(
    (stats?.top_clients ?? []).map((c) => ({
      label: companyName(c.company_id),
      value: c.revenue,
    })),
  );
</script>

<svelte:head>
  <title>{t("overview.revenue.title")}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-end justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-neutral-900">{t("overview.revenue.title")}</h1>
    <p class="mt-1 text-sm text-neutral-500">{t("overview.revenue.subtitle")}</p>
  </div>
  <div class="flex items-center gap-2 text-sm" data-sveltekit-preload-data="hover">
    <a href={`?year=${data.year - 1}`}
      class="rounded-lg border border-neutral-300 px-2 py-1 hover:bg-neutral-50" aria-label="←">←</a>
    <span class="font-semibold tabular-nums text-neutral-800">{data.year}</span>
    <a href={`?year=${data.year + 1}`}
      class="rounded-lg border border-neutral-300 px-2 py-1 hover:bg-neutral-50" aria-label="→">→</a>
  </div>
</div>

{#if stats}
  <!-- Totals -->
  <div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
    <div class="rounded-xl border border-neutral-200 bg-white p-4">
      <p class="text-xs text-neutral-500">{t("overview.revenue.total_year", { year: data.year })}</p>
      <p class="mt-1 text-lg font-semibold tabular-nums text-neutral-900">{fmtMoney(stats.total_current)}</p>
    </div>
    <div class="rounded-xl border border-neutral-200 bg-white p-4">
      <p class="text-xs text-neutral-500">{t("overview.revenue.total_year", { year: data.year - 1 })}</p>
      <p class="mt-1 text-lg font-semibold tabular-nums text-neutral-900">{fmtMoney(stats.total_previous)}</p>
    </div>
    <div class="rounded-xl border border-neutral-200 bg-white p-4">
      <p class="text-xs text-neutral-500">{t("overview.revenue.delta")}</p>
      <p class="mt-1 text-lg font-semibold tabular-nums {delta == null ? 'text-neutral-400' : delta >= 0 ? 'text-green-600' : 'text-red-600'}">
        {delta == null ? "—" : `${delta >= 0 ? "+" : ""}${delta}%`}
      </p>
    </div>
  </div>

  <!-- Monthly comparison -->
  <section class="mb-4 rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("overview.revenue.monthly")}</h2>
    <MonthlyComparisonChart
      current={stats.months_current}
      previous={stats.months_previous}
      currentLabel={String(data.year)}
      previousLabel={String(data.year - 1)}
    />
  </section>

  <!-- Top clients -->
  <section class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-4 text-sm font-semibold text-neutral-900">
      {t("overview.revenue.top_clients", { year: data.year })}
    </h2>
    {#if slices.length === 0}
      <p class="text-sm text-neutral-500">{t("overview.revenue.empty")}</p>
    {:else}
      <DonutChart
        {slices}
        otherLabel={t("overview.revenue.other")}
        otherValue={stats.other_revenue}
        centerLabel={t("overview.revenue.center_label")}
      />
    {/if}
    <p class="mt-4 text-xs text-neutral-400">{t("overview.revenue.rate_hint")}</p>
  </section>
{/if}
