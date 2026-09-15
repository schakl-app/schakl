<script lang="ts">
  /**
   * A sortable, pageable table for a widget's rows — the page table, the funnel, the pivot, the
   * campaign tables — with a CSV export and the conditional formatting the column declares.
   *
   * Sorting and paging are local: the API caps the rows and says so (`row_count`), and a table
   * of at most a hundred rows is sorted in the browser, not by another round trip. A cell drawn
   * red is one the column's own threshold flagged (`alarm_above`), never a colour picked here:
   * red is for faults and critical dropout only (docs/UX.md, spec §11).
   */
  import { ArrowDown, ArrowUp, Download } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { orgToday } from "$lib/core/today";

  import {
    cellTone,
    cellValue,
    columnTitle,
    downloadCsv,
    fmtCell,
    widgetCsv,
    widgetTitle,
  } from "./format";
  import type { LeadColumn, LeadRow, LeadWidget } from "./types";

  let {
    widget,
    labelHeader,
    activeKeys = [],
    filterHref,
    pageSize = 15,
  }: {
    widget: LeadWidget;
    /** The first column's heading: the dimension's title, or a sentence about the rows. */
    labelHeader: string;
    activeKeys?: string[];
    filterHref?: ((dimension: string, key: string) => string) | undefined;
    pageSize?: number;
  } = $props();

  let sortKey = $state<string | null>(null);
  let sortDesc = $state(true);
  let page = $state(0);

  function sortBy(key: string) {
    if (sortKey === key) sortDesc = !sortDesc;
    else {
      sortKey = key;
      sortDesc = key !== "__label";
    }
    page = 0;
  }

  const columns = $derived(widget.columns);
  const sorted = $derived.by(() => {
    const rows = [...widget.rows];
    if (!sortKey) return rows;
    const column = columns.find((c) => c.key === sortKey);
    const read = (row: LeadRow): number | string =>
      sortKey === "__label" ? row.label : column ? (cellValue(row, column) ?? -Infinity) : 0;
    rows.sort((a, b) => {
      const va = read(a);
      const vb = read(b);
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb));
      return sortDesc ? -cmp : cmp;
    });
    return rows;
  });
  const pages = $derived(Math.max(1, Math.ceil(sorted.length / pageSize)));
  const visible = $derived(sorted.slice(page * pageSize, (page + 1) * pageSize));
  const filterable = $derived(Boolean(filterHref && widget.dimension));

  function toneClass(row: LeadRow, column: LeadColumn): string {
    const tone = cellTone(row, column);
    if (tone === "alarm") return "font-semibold text-red-600 dark:text-red-400";
    if (tone === "warn") return "text-amber-600 dark:text-amber-400";
    return "text-text";
  }

  function exportCsv() {
    downloadCsv(`${widget.key}-${orgToday()}.csv`, widgetCsv(widget, labelHeader));
  }
</script>

{#if widget.rows.length === 0}
  <p class="text-sm text-text-muted">{t("marketing.no_data")}</p>
{:else}
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-left text-xs text-text-muted">
          <th class="py-1.5 pr-2 font-medium">
            <button
              type="button"
              class="flex items-center gap-1 hover:text-text"
              onclick={() => sortBy("__label")}
            >
              {labelHeader}
              {#if sortKey === "__label"}
                {#if sortDesc}<ArrowDown size={12} />{:else}<ArrowUp size={12} />{/if}
              {/if}
            </button>
          </th>
          {#each columns as column (column.key)}
            <th
              class="py-1.5 pl-2 font-medium {column.unit === 'text' ? 'text-left' : 'text-right'}"
            >
              <button
                type="button"
                class="inline-flex items-center gap-1 hover:text-text"
                onclick={() => sortBy(column.key)}
              >
                {columnTitle(column)}
                {#if sortKey === column.key}
                  {#if sortDesc}<ArrowDown size={12} />{:else}<ArrowUp size={12} />{/if}
                {/if}
              </button>
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each visible as row (row.key)}
          {@const active = activeKeys.includes(row.key)}
          <tr class="border-b border-border/50 {active ? 'bg-surface' : ''}">
            <td class="max-w-[18rem] py-1.5 pr-2 text-text">
              {#if filterable && widget.dimension}
                <a
                  href={filterHref?.(widget.dimension, row.key)}
                  data-sveltekit-noscroll
                  class="block truncate hover:text-brand {active ? 'font-medium' : ''}"
                  title={t("marketing.leads.filter.click_to_filter")}
                >
                  {row.label}
                </a>
              {:else}
                <span class="block truncate" title={row.label}>{row.label}</span>
              {/if}
            </td>
            {#each columns as column (column.key)}
              <td
                class="py-1.5 pl-2 align-top tabular-nums {column.unit === 'text'
                  ? 'text-left text-text-muted'
                  : 'text-right'} {toneClass(row, column)}"
              >
                {fmtCell(row, column, widget.currency)}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
    <span>
      {#if widget.row_count}
        {t("marketing.leads.table.capped", {
          shown: String(widget.rows.length),
          total: String(widget.row_count),
        })}
      {:else if pages > 1}
        {t("marketing.leads.table.page", { page: String(page + 1), pages: String(pages) })}
      {/if}
    </span>
    <span class="flex items-center gap-2">
      {#if pages > 1}
        <button
          type="button"
          class="rounded border border-border px-2 py-0.5 hover:border-brand disabled:opacity-40"
          disabled={page === 0}
          onclick={() => (page = Math.max(0, page - 1))}
        >
          {t("common.previous")}
        </button>
        <button
          type="button"
          class="rounded border border-border px-2 py-0.5 hover:border-brand disabled:opacity-40"
          disabled={page >= pages - 1}
          onclick={() => (page = Math.min(pages - 1, page + 1))}
        >
          {t("common.next")}
        </button>
      {/if}
      <button
        type="button"
        class="flex items-center gap-1 rounded border border-border px-2 py-0.5 hover:border-brand hover:text-brand"
        onclick={exportCsv}
        aria-label={t("marketing.leads.table.export", { title: widgetTitle(widget) })}
      >
        <Download size={12} />
        CSV
      </button>
    </span>
  </div>
{/if}
