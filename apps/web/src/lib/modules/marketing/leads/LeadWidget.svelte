<script lang="ts">
  /**
   * One widget, drawn by its `kind`. The shape is the API's rendering instruction; every
   * shape reads the same rows, so a funnel is a table with thresholds and a pivot is a table
   * with tenant-named columns, and the reader can take any of them apart (UX Principle 7).
   */
  import { t } from "$lib/core/i18n";
  import ComboChart from "$lib/core/ui/charts/ComboChart.svelte";
  import DonutChart from "$lib/core/ui/charts/DonutChart.svelte";
  import TrendChart from "$lib/core/ui/charts/TrendChart.svelte";
  import { PANEL_HEADING } from "$lib/core/ui/headings";

  import { channelGroupLabel, fmtUnit, widgetTitle } from "./format";
  import LeadBars from "./LeadBars.svelte";
  import LeadScorecard from "./LeadScorecard.svelte";
  import LeadTable from "./LeadTable.svelte";
  import type { LeadBreakpoint, LeadWidget } from "./types";

  let {
    widget,
    activeFilters = {},
    filterHref,
    breakpoints = [],
  }: {
    widget: LeadWidget;
    activeFilters?: Record<string, string[]>;
    filterHref?: ((dimension: string, key: string) => string) | undefined;
    breakpoints?: LeadBreakpoint[];
  } = $props();

  const activeKeys = $derived(widget.dimension ? (activeFilters[widget.dimension] ?? []) : []);
  const labelHeader = $derived(
    widget.dimension_title ??
      (widget.dimension
        ? t(`marketing.leads.dimension.${widget.dimension}`)
        : t("marketing.leads.col.row")),
  );
  const markers = $derived(
    breakpoints.map((b) => ({ date: b.date, label: b.text ?? t("marketing.leads.breakpoint") })),
  );
  const donutSlices = $derived(
    widget.rows.map((r) => ({
      label: r.group ? `${r.label} · ${channelGroupLabel(r.group)}` : r.label,
      value: r.values.count ?? 0,
    })),
  );
</script>

{#if widget.kind === "scorecard"}
  <LeadScorecard {widget} />
{:else}
  <!-- `h-full`: two halves of one grid row end on one line, whichever is the longer. -->
  <div class="h-full rounded-lg border border-border p-3">
    <div class="mb-2 flex items-baseline justify-between gap-2">
      <h3 class={PANEL_HEADING}>{widgetTitle(widget)}</h3>
      {#if widget.total !== null && widget.total !== undefined && widget.kind !== "combo"}
        <span class="text-xs tabular-nums text-text-muted">
          {t("marketing.leads.total", { value: fmtUnit(widget.total, "count") })}
        </span>
      {/if}
    </div>
    {#if widget.kind === "bars"}
      <LeadBars {widget} {activeKeys} {filterHref} />
    {:else if widget.kind === "line" && widget.series}
      <TrendChart
        dates={widget.series.dates}
        values={widget.series.values.requests ?? []}
        label={widgetTitle(widget)}
        format={(v) => fmtUnit(v, "count")}
        {markers}
      />
    {:else if widget.kind === "combo" && widget.series}
      {@const barKey = widget.series.bars[0] ?? "cost"}
      {@const lineKey =
        Object.keys(widget.series.values).find((k) => k !== barKey) ?? "conversions"}
      <ComboChart
        dates={widget.series.dates}
        bars={widget.series.values[barKey] ?? []}
        line={widget.series.values[lineKey] ?? []}
        barLabel={t(`marketing.leads.series.${barKey}`)}
        lineLabel={t(`marketing.leads.series.${lineKey}`)}
        formatBar={(v) => fmtUnit(v, widget.series?.units[barKey] ?? "count", widget.currency)}
        formatLine={(v) => fmtUnit(v, widget.series?.units[lineKey] ?? "count", widget.currency)}
      />
    {:else if widget.kind === "donut"}
      {#if widget.rows.length === 0}
        <p class="text-sm text-text-muted">{t("marketing.no_data")}</p>
      {:else}
        <DonutChart
          slices={donutSlices}
          otherLabel={t("marketing.leads.other")}
          otherValue={widget.other ?? 0}
          centerLabel={t("marketing.leads.widget.requests")}
          format={(v) => fmtUnit(v, "count")}
        />
      {/if}
    {:else}
      <LeadTable {widget} {labelHeader} {activeKeys} {filterHref} />
    {/if}
    {#if widget.note_key}
      <p class="mt-2 text-[11px] leading-snug text-text-muted">{t(widget.note_key)}</p>
    {/if}
  </div>
{/if}
