<script lang="ts">
  /**
   * One source's section on the marketing tab (issue #133): KPI tiles for every metric, a trend
   * chart with a metric switcher, the GA4 acquisition split, and the live drill-downs. Renders the
   * KPIs/trend from stored data (instant); only the drill-downs touch Google, lazily.
   */
  import { ExternalLink } from "@lucide/svelte";

  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import TrendChart from "$lib/core/ui/charts/TrendChart.svelte";

  import MarketingDrilldown from "./MarketingDrilldown.svelte";
  import { deltaClass, deltaView, fmtMetric, metricLabel, sourceLabel } from "./format";
  import { ALL_METRICS, DRILLDOWNS, type SourceMetrics } from "./types";

  let {
    companyId,
    src,
    rangeDays,
  }: {
    companyId: string;
    src: SourceMetrics;
    rangeDays: number;
  } = $props();

  // The charted metric: the source's primary until the user picks another. A plain override
  // (not a reset-on-src effect) keeps the choice when only the *range* changes — the section is
  // keyed by link_id, so `src` is always the same source here.
  let override = $state<string | null>(null);
  const selected = $derived(override ?? src.primary_metric);

  const metrics = $derived(ALL_METRICS[src.source] ?? []);
  // The API withholds the keyEvents KPI when this client's key events are hidden (#134);
  // its absence also hides the by-event drill-down (the endpoint 422s it anyway).
  const drilldowns = $derived(
    (DRILLDOWNS[src.source] ?? []).filter(
      (kind) => kind !== "key_events" || "keyEvents" in (src.kpis ?? {}),
    ),
  );
  const values = $derived(src.series?.metrics?.[selected] ?? []);
  const dates = $derived(src.series?.dates ?? []);

  const channelEntries = $derived(
    src.channels ? Object.entries(src.channels).sort((a, b) => b[1] - a[1]) : [],
  );
  const channelMax = $derived(Math.max(...channelEntries.map(([, v]) => v), 1));
</script>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
    <div class="flex items-center gap-2">
      <h2 class="text-base font-semibold text-text">{sourceLabel(src.source)}</h2>
      <span class="truncate text-sm text-text-muted">{src.display_name}</span>
    </div>
    {#if src.deep_link}
      <a
        href={src.deep_link}
        target="_blank"
        rel="noopener noreferrer"
        class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
      >
        {t("marketing.open_in", { source: sourceLabel(src.source) })}
        <ExternalLink size={12} />
      </a>
    {/if}
  </div>

  {#if src.health === "pending"}
    <p class="text-sm text-text-muted">{t("marketing.pending_hint")}</p>
  {:else if src.health === "disconnected"}
    <p class="text-sm text-red-600 dark:text-red-400">{t("marketing.disconnected")}</p>
  {:else}
    <!-- KPI tiles: every metric the source carries, with its period-over-period delta. -->
    <div class="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {#each metrics as key (key)}
        {@const kpi = src.kpis?.[key]}
        {#if kpi}
          {@const delta = deltaView(kpi.delta_pct, kpi.lower_is_better)}
          <button
            type="button"
            onclick={() => (override = key)}
            class="rounded-lg border p-3 text-left transition-colors {selected === key
              ? 'border-brand bg-surface'
              : 'border-border hover:border-brand/50'}"
          >
            <p class="text-xs text-text-muted">{metricLabel(key)}</p>
            <p class="mt-0.5 text-lg font-semibold tabular-nums text-text">
              {fmtMetric(key, kpi.current, src.currency)}
            </p>
            {#if delta}
              <p class="text-xs tabular-nums {deltaClass(delta.tone)}">
                {delta.text} <span class="text-text-muted">{t("marketing.vs_previous")}</span>
              </p>
            {/if}
          </button>
        {/if}
      {/each}
    </div>

    <!-- Trend of the selected metric. -->
    <div class="mb-5">
      <p class="mb-1 text-sm font-medium text-text">{metricLabel(selected)}</p>
      <TrendChart
        {dates}
        {values}
        label={metricLabel(selected)}
        format={(v) => fmtMetric(selected, v, src.currency)}
      />
    </div>

    {#if channelEntries.length}
      <div class="mb-5">
        <p class="mb-2 text-sm font-medium text-text">{t("marketing.channels_title")}</p>
        <ul class="space-y-1.5">
          {#each channelEntries as [name, value] (name)}
            <li class="flex items-center gap-2 text-sm">
              <span class="w-32 shrink-0 truncate text-text-muted">{name}</span>
              <span class="h-2 flex-1 overflow-hidden rounded-full bg-surface">
                <span
                  class="block h-full rounded-full bg-brand"
                  style="width: {(value / channelMax) * 100}%"
                ></span>
              </span>
              <span class="w-16 shrink-0 text-right tabular-nums text-text">{fmtNumber(value, 0)}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <!-- Live drill-downs (only these touch Google), keyed so a range change re-fetches. -->
    <div class="grid gap-5 md:grid-cols-2">
      {#each drilldowns as kind (kind)}
        {#key rangeDays}
          <MarketingDrilldown
            {companyId}
            linkId={src.link_id}
            source={src.source}
            {kind}
            {rangeDays}
            currency={src.currency}
          />
        {/key}
      {/each}
    </div>
  {/if}
</section>
