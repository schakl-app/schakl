<script lang="ts">
  /**
   * Overzicht → Projecten: budgets, hours and what those hours are worth, per project.
   *
   * The strip is the whole set's burn bands (#407: never the page's), and each of its three
   * headings opens this list filtered to that band. The table is the shared `DataTable` —
   * server sort, personal columns, the pager — with the budget half from the projects API and
   * the hours half from the time module, joined in the load (`report-columns.ts`).
   */
  import { delta, sharePct } from "$lib/core/delta";
  import { fmtMoney, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import HoursCell from "$lib/core/ui/HoursCell.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import SummaryStrip from "$lib/core/ui/SummaryStrip.svelte";
  import { BURN_GROUPS, burnGroupLabelKey } from "$lib/modules/projects/burn-groups";
  import { PROJECT_REPORT_COLUMNS } from "$lib/modules/projects/report-columns";
  import { PROJECT_STATUS_ALL, statusPillClass } from "$lib/modules/projects/status";
  import { hoursFromMinutes } from "$lib/modules/time/format";

  import type { ComponentProps } from "svelte";

  type SummaryTile = ComponentProps<typeof SummaryStrip>["tiles"][number];

  let { data } = $props();

  type Row = (typeof data.rows)[number];

  const table = createTableLayout<Row>({
    all: () => PROJECT_REPORT_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      project: projectCell,
      client: clientCell,
      status: statusCell,
      budget: budgetCell,
      hours: hoursCell,
      billable: billableCell,
      invoiced: invoicedCell,
      value: valueCell,
      budget_amount: budgetAmountCell,
    }),
  });

  const hoursText = (minutes: number | undefined | null) =>
    minutes == null ? "—" : t("hours.spent", { hours: fmtNumber(hoursFromMinutes(minutes), 1) });

  // The shared bar (#354): the running/all pills and the burn band, in the words the strip's
  // tiles use — a filter you can arrive at by link must be visible and clearable.
  const filterDefs: FilterDef[] = $derived([
    {
      kind: "pills",
      key: "status",
      options: [
        { value: "", label: t("overview.projects.filter.running") },
        { value: PROJECT_STATUS_ALL, label: t("overview.projects.filter.all") },
      ],
    },
    {
      kind: "pills",
      key: "burn",
      options: BURN_GROUPS.map((level) => ({ value: level, label: t(burnGroupLabelKey(level)) })),
    },
  ]);
  const filterHref = (status: string, burn: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (burn) params.set("burn", burn);
    const s = params.toString();
    return s ? `?${s}` : "?";
  };

  const tiles = $derived.by((): SummaryTile[] => {
    const out: SummaryTile[] = [];
    const b = data.budgets;
    if (b) {
      out.push(
        {
          key: "over",
          label_key: "projects.filter.burn.over",
          value: String(b.over_budget),
          format: "number",
          tone: b.over_budget > 0 ? "bad" : "neutral",
          hint_key: b.over_budget > 0 ? "overview.projects.hint.over_hours" : undefined,
          hint_params: { hours: fmtNumber(b.over_budget_hours, 1) },
          href: filterHref(data.statusFilter, "over"),
        },
        {
          key: "warn",
          label_key: "projects.filter.burn.warn",
          value: String(b.almost_budget),
          format: "number",
          tone: b.almost_budget > 0 ? "warn" : "neutral",
          href: filterHref(data.statusFilter, "warn"),
        },
        {
          key: "ok",
          label_key: "projects.filter.burn.ok",
          value: String(b.within_budget),
          format: "number",
          tone: b.within_budget > 0 ? "good" : "neutral",
          hint_key: "overview.hint.budgets",
          hint_params: { count: b.total },
          href: filterHref(data.statusFilter, "ok"),
        },
      );
    }
    const totals = data.totals;
    // Nothing is a number: with no rate anywhere the worth is unknown, not zero, so the tile is
    // left out and the hours it would have priced are still counted on the invoiced tile.
    if (totals.billable_minutes > 0 && totals.billable_amount > 0) {
      out.push({
        key: "value",
        label_key: "overview.projects.tile.value",
        value: String(totals.billable_amount),
        format: "money",
        hint_key:
          totals.unrated_minutes > 0
            ? "overview.projects.tile.value_unrated"
            : "overview.projects.tile.value_hint",
        hint_params: {
          hours: fmtNumber(hoursFromMinutes(totals.billable_minutes), 1),
          unrated: fmtNumber(hoursFromMinutes(totals.unrated_minutes), 1),
        },
      });
    }
    if (totals.billable_minutes > 0) {
      out.push({
        key: "invoiced",
        label_key: "overview.projects.tile.invoiced",
        value: String(hoursFromMinutes(totals.invoiced_minutes)),
        format: "hours",
        hint_key: "overview.projects.tile.invoiced_hint",
        hint_params: { pct: sharePct(totals.invoiced_minutes, totals.billable_minutes) },
        href: "/overview/hours?status=invoiced",
      });
    }
    return out;
  });
</script>

{#snippet projectCell(row: Row)}
  <a href={`/projects/${row.id}`} class="block truncate font-medium text-text hover:text-brand">
    {row.name}
  </a>
{/snippet}

{#snippet clientCell(row: Row)}
  {#if row.company_id}
    <a href={`/companies/${row.company_id}`} class="block truncate text-text hover:text-brand">
      {row.company_name ?? ""}
    </a>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet statusCell(row: Row)}
  <span class="rounded-full px-2 py-0.5 text-xs font-medium {statusPillClass(row.status)}">
    {t(`projects.status.${row.status}`)}
  </span>
{/snippet}

{#snippet budgetCell(row: Row)}
  <HoursCell hours={row.hours} />
{/snippet}

{#snippet hoursCell(row: Row)}
  <span class="tabular-nums text-text">{hoursText(row.time?.minutes)}</span>
{/snippet}

{#snippet billableCell(row: Row)}
  {#if row.time}
    <span class="tabular-nums text-text">{hoursText(row.time.billable_minutes)}</span>
    {#if row.time.minutes > 0}
      <span class="ml-1 text-xs tabular-nums text-text-muted">
        {sharePct(row.time.billable_minutes, row.time.minutes)}%
      </span>
    {/if}
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet invoicedCell(row: Row)}
  {#if row.time}
    <a
      href={`/overview/hours?project_id=${row.id}&status=invoiced`}
      class="tabular-nums text-text hover:text-brand">{hoursText(row.time.invoiced_minutes)}</a
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet valueCell(row: Row)}
  {#if row.time && row.time.billable_minutes > 0 && row.time.billable_amount === 0}
    <!-- Every billable hour was logged by somebody without a rate: there is no worth to print,
         and "€ 0" would read as a verdict on the project rather than on the setup. -->
    <span
      class="text-text-muted"
      title={t("overview.projects.unrated", {
        hours: fmtNumber(hoursFromMinutes(row.time.unrated_minutes), 1),
      })}>—</span
    >
  {:else if row.time && row.time.billable_minutes > 0}
    {@const over = delta(row.time.billable_amount, row.budget_amount ?? null)}
    <span
      class="tabular-nums text-text"
      title={row.time.unrated_minutes > 0
        ? t("overview.projects.unrated", {
            hours: fmtNumber(hoursFromMinutes(row.time.unrated_minutes), 1),
          })
        : undefined}>{fmtMoney(row.time.billable_amount)}</span
    >
    {#if row.time.unrated_minutes > 0}
      <span class="ml-1 text-xs text-text-muted" aria-hidden="true">*</span>
    {/if}
    {#if row.budget_amount != null && over && over.pct > 0}
      <span class="ml-1 text-xs tabular-nums text-red-700 dark:text-red-400">{over.text}</span>
    {/if}
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet budgetAmountCell(row: Row)}
  <span class="tabular-nums {row.budget_amount != null ? 'text-text' : 'text-text-muted'}">
    {row.budget_amount != null ? fmtMoney(row.budget_amount) : "—"}
  </span>
{/snippet}

{#snippet empty()}
  <p class="text-sm text-text-muted">{t("overview.projects.empty")}</p>
{/snippet}

{#snippet mobileRow(row: Row)}
  <a
    href={`/projects/${row.id}`}
    class="block rounded-lg border border-border bg-surface-raised p-3"
  >
    <p class="font-medium text-text">{row.name}</p>
    {#if row.company_name}
      <p class="text-xs text-text-muted">{row.company_name}</p>
    {/if}
    <div class="mt-1 text-sm"><HoursCell hours={row.hours} /></div>
    {#if row.time}
      <p class="mt-1 text-xs text-text-muted">
        {t("overview.projects.column.hours")}: {hoursText(row.time.minutes)} ·
        {t("overview.projects.column.value")}: {fmtMoney(row.time.billable_amount)}
      </p>
    {/if}
  </a>
{/snippet}

<svelte:head>
  <title>{pageTitle(t("overview.projects.title"))}</title>
</svelte:head>

<PageHeader title={t("overview.projects.title")}>
  {#snippet subtitle()}{t("overview.projects.subtitle")}{/snippet}
</PageHeader>

<SummaryStrip {tiles} />

<FilterBar filters={filterDefs} idPrefix="overview-projects-filter">
  {#snippet actions()}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
  {/snippet}
</FilterBar>

<DataTable
  rows={data.rows}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  rowHref={(row) => `/projects/${row.id}`}
  onsort={table.onSort}
  onresize={table.onResize}
  {empty}
  {mobileRow}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<p class="mt-3 text-xs text-text-muted">{t("overview.projects.note")}</p>
