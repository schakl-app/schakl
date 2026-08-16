<script lang="ts">
  /**
   * The period against its comparison, drawn from schakl's own nightly mirror.
   *
   * Two rendering rules that the API already enforces and that this must not undo:
   *
   * - **A change with no baseline shows the absolute figure and no percentage.** The API sends
   *   `relative: null` when the period it is compared against was zero, because a percentage
   *   against nothing is undefined — and "+∞ %" is a thing somebody eventually reads out to a
   *   client.
   * - **Lower is better for the cost metrics.** A CPA that fell is good news and must not be
   *   drawn as a decline. The direction is carried by an arrow and a word, never by colour
   *   alone: `text-brand` is gold on some tenants and would read as a warning on every tile.
   */
  import { ArrowDown, ArrowUp, Minus } from "@lucide/svelte";

  import { dateLocale, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { GoogleAdsMetrics } from "./types";

  interface ChangeAmount {
    from?: number | null;
    to?: number | null;
    absolute?: number | null;
    relative?: number | null;
  }

  let {
    totals,
    previous,
    change,
    breakdown = [],
    currency = null,
  }: {
    totals: GoogleAdsMetrics | null;
    previous: GoogleAdsMetrics | null;
    change: Record<string, ChangeAmount | null>;
    breakdown?: Record<string, unknown>[];
    currency?: string | null;
  } = $props();

  /** The tiles, in reading order: what it cost, what it produced, what each one cost. */
  const TILES = [
    { key: "cost", kind: "money" as const, lowerIsBetter: true },
    { key: "clicks", kind: "number" as const, lowerIsBetter: false },
    { key: "conversions", kind: "number" as const, lowerIsBetter: false },
    { key: "cost_per_conversion", kind: "money" as const, lowerIsBetter: true },
  ];

  const money = $derived((value: number) =>
    new Intl.NumberFormat(
      dateLocale(),
      currency
        ? { style: "currency", currency, maximumFractionDigits: 2 }
        : { style: "decimal", maximumFractionDigits: 2 },
    ).format(value),
  );

  function show(kind: "money" | "number", value: unknown): string {
    // The dash *is* the rendering of "not computable" — reached before any coercion, because
    // `Number(null)` is 0 and that is the lie this guard exists to prevent.
    if (value === null || value === undefined) return "–";
    return kind === "money" ? money(Number(value)) : fmtNumber(Number(value));
  }
</script>

{#if !totals}
  <p class="text-sm text-text-muted">{t("google_ads.trend.no_data")}</p>
{:else}
  <div class="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    {#each TILES as tile (tile.key)}
      {@const amount = change[tile.key]}
      {@const moved = amount?.absolute ?? 0}
      {@const better = tile.lowerIsBetter ? moved < 0 : moved > 0}
      <div class="rounded-xl border border-border bg-surface-raised p-4">
        <span class="block text-xs text-text-muted">{t(`google_ads.metric.${tile.key}`)}</span>
        <span class="mt-1 block text-lg font-semibold text-text tabular-nums">
          {show(tile.kind, (totals as unknown as Record<string, unknown>)[tile.key])}
        </span>
        {#if amount && amount.relative !== null && amount.relative !== undefined}
          <span class="mt-1 flex items-center gap-1 text-xs text-text-muted">
            {#if moved > 0}
              <ArrowUp size={12} aria-hidden="true" />
            {:else if moved < 0}
              <ArrowDown size={12} aria-hidden="true" />
            {:else}
              <Minus size={12} aria-hidden="true" />
            {/if}
            <span class="tabular-nums">{fmtNumber(Math.abs(amount.relative) * 100, 1)} %</span>
            <!-- The word, not just the arrow: a CPA that fell is good news, and an arrow alone
                 cannot say so. -->
            <span>{better ? t("google_ads.trend.better") : t("google_ads.trend.worse")}</span>
          </span>
        {:else if amount}
          <!-- No baseline to compare against — the absolute figure, and no percentage. -->
          <span class="mt-1 block text-xs text-text-muted">
            {t("google_ads.trend.no_baseline")}
          </span>
        {/if}
        {#if previous}
          <span class="mt-1 block text-xs text-text-muted">
            {t("google_ads.trend.was")}
            {show(tile.kind, (previous as unknown as Record<string, unknown>)[tile.key])}
          </span>
        {/if}
      </div>
    {/each}
  </div>

  {#if breakdown.length > 0}
    <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
      <table class="w-full min-w-max text-sm">
        <thead>
          <tr class="border-b border-border text-left">
            <th class="px-3 py-2 text-xs font-medium text-text-muted">
              {t("google_ads.column.campaign")}
            </th>
            {#each TILES as tile (tile.key)}
              <th class="px-3 py-2 text-right text-xs font-medium text-text-muted">
                {t(`google_ads.metric.${tile.key}`)}
              </th>
            {/each}
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each breakdown as row, index (index)}
            <tr>
              <td class="px-3 py-2">{row.campaign_name}</td>
              {#each TILES as tile (tile.key)}
                <td class="px-3 py-2 text-right tabular-nums">
                  {show(tile.kind, row[tile.key])}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
