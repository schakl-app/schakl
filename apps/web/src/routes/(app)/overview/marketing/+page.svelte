<script lang="ts">
  /**
   * Overzicht → Marketing (issue #133): the morning-coffee grid over every linked client, read
   * from the stored daily aggregates (one query), server-sorted via the shared DataTable's column
   * descriptors. Each metric cell carries its period-over-period delta so "who moved" reads at a
   * glance; the client cell opens that client's marketing tab.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import { MARKETING_OVERVIEW_COLUMNS } from "$lib/modules/marketing/columns";
  import { deltaClass, deltaView, fmtMetric, sourceLabel } from "$lib/modules/marketing/format";
  import type { KpiValue, OverviewRow } from "$lib/modules/marketing/types";

  let { data } = $props();

  interface Row extends OverviewRow {
    id: string;
  }
  const rows = $derived<Row[]>(
    (data.overview.rows ?? []).map((r) => ({ ...(r as OverviewRow), id: r.company_id })),
  );

  const table = createTableLayout<Row>({
    all: () => MARKETING_OVERVIEW_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      company: companyCell,
      sources: sourcesCell,
      sessions: sessionsCell,
      clicks: clicksCell,
      position: positionCell,
      cost: costCell,
      conversions: conversionsCell,
      key_events: keyEventsCell,
    }),
  });

  const canManage = $derived(Boolean(data.canManage));

  const RANGES = ["30d", "month", "quarter", "90d", "yoy"] as const;
  const rangeClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

{#snippet kpiCell(kpi: KpiValue | undefined, key: string)}
  {#if kpi}
    {@const delta = deltaView(kpi.delta_pct, kpi.lower_is_better)}
    <span class="tabular-nums text-text">{fmtMetric(key, kpi.current)}</span>
    {#if delta}
      <span class="ml-1 text-xs tabular-nums {deltaClass(delta.tone)}">{delta.text}</span>
    {/if}
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet companyCell(row: Row)}
  <a
    href={`/companies/${row.company_id}/marketing`}
    class="font-medium text-brand hover:underline"
  >
    {row.company_name}
  </a>
{/snippet}

{#snippet sourcesCell(row: Row)}
  <span class="flex flex-wrap gap-1">
    {#each row.sources_present as s (s)}
      <span class="rounded bg-surface px-1.5 py-0.5 text-xs text-text-muted">{sourceLabel(s)}</span>
    {/each}
  </span>
{/snippet}

{#snippet sessionsCell(row: Row)}{@render kpiCell(row.metrics.sessions, "sessions")}{/snippet}
{#snippet clicksCell(row: Row)}{@render kpiCell(row.metrics.clicks, "clicks")}{/snippet}
{#snippet positionCell(row: Row)}{@render kpiCell(row.metrics.position, "position")}{/snippet}
{#snippet costCell(row: Row)}{@render kpiCell(row.metrics.cost, "cost")}{/snippet}
{#snippet conversionsCell(row: Row)}{@render kpiCell(row.metrics.conversions, "conversions")}{/snippet}

{#snippet keyEventsCell(row: Row)}
  {#if !row.sources_present.includes("ga4")}
    <!-- No GA4 for this client, so key events are not a thing to show or hide. -->
    <span class="text-text-muted">—</span>
  {:else if canManage}
    <!-- Interactive cell is safe here: the grid navigates only via the company cell's own link,
         never a whole-row click, so this switch keeps its own action. -->
    <form method="POST" action="?/toggleKeyEvents" use:enhance class="inline-flex">
      <input type="hidden" name="company_id" value={row.company_id} />
      <input type="hidden" name="show_key_events" value={(!row.show_key_events).toString()} />
      <button
        type="submit"
        role="switch"
        aria-checked={row.show_key_events}
        aria-label={t("marketing.settings.key_events_label")}
        class="relative z-10 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors {row.show_key_events
          ? 'bg-brand'
          : 'border border-border bg-surface'}"
      >
        <span
          class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform {row.show_key_events
            ? 'translate-x-4'
            : 'translate-x-0.5'}"
        ></span>
      </button>
    </form>
  {:else}
    <span class="text-xs {row.show_key_events ? 'text-text' : 'text-text-muted'}">
      {row.show_key_events ? t("marketing.settings.on") : t("marketing.settings.off")}
    </span>
  {/if}
{/snippet}

{#snippet empty()}
  <p class="text-sm text-text-muted">{t("marketing.overview.empty")}</p>
{/snippet}

{#snippet mobileRow(row: Row)}
  <a
    href={`/companies/${row.company_id}/marketing`}
    class="block rounded-lg border border-border bg-surface-raised p-3"
  >
    <p class="font-medium text-text">{row.company_name}</p>
    <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-text-muted">
      {#if row.metrics.sessions}
        <span>{t("marketing.metric.sessions")}: {fmtMetric("sessions", row.metrics.sessions.current)}</span>
      {/if}
      {#if row.metrics.clicks}
        <span>{t("marketing.metric.clicks")}: {fmtMetric("clicks", row.metrics.clicks.current)}</span>
      {/if}
      {#if row.metrics.cost}
        <span>{t("marketing.metric.cost")}: {fmtMetric("cost", row.metrics.cost.current)}</span>
      {/if}
    </div>
    {#if row.sources_present.includes("ga4")}
      <p class="mt-1 text-xs text-text-muted">
        {t("marketing.overview.column.key_events")}:
        {row.show_key_events ? t("marketing.settings.on") : t("marketing.settings.off")}
      </p>
    {/if}
  </a>
{/snippet}

<svelte:head>
  <title>{pageTitle(t("marketing.overview.title"))}</title>
</svelte:head>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{t("marketing.overview.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("marketing.overview.subtitle")}</p>
</div>

<div class="mb-4 flex flex-wrap items-center justify-between gap-2">
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#each RANGES as r (r)}
      <a href={`?range=${r}`} class={rangeClass(data.range === r)} data-sveltekit-noscroll>
        {t(`marketing.range.${r}`)}
      </a>
    {/each}
  </div>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

<DataTable
  {rows}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  rowHref={(row) => `/companies/${row.company_id}/marketing`}
  onsort={table.onSort}
  onresize={table.onResize}
  {empty}
  {mobileRow}
/>
