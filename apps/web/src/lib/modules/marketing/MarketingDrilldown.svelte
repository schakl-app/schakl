<script lang="ts">
  /**
   * One live drill-down (top pages/queries/campaigns), lazy-loaded (issue #133).
   *
   * The tab renders its stored trends instantly, then each drill-down fetches from the
   * `/marketing/drilldown` proxy on mount so a slow/failing Google call never blocks the page. The
   * API caches ~1h and returns a labelled `unavailable` state (no scope / Ads token / revoked),
   * which we show with a deep link out to the real tool.
   */
  import { ExternalLink } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import { drilldownLabel, fmtMetric, metricLabel, sourceLabel } from "./format";
  import type { DrilldownResponse, MarketingSource } from "./types";

  let {
    companyId,
    linkId,
    source,
    kind,
    rangeDays,
    currency,
  }: {
    companyId: string;
    linkId: string;
    source: MarketingSource;
    kind: string;
    rangeDays: number;
    currency: string | null;
  } = $props();

  let loading = $state(true);
  let data = $state<DrilldownResponse | null>(null);

  async function load() {
    loading = true;
    try {
      const params = new URLSearchParams({
        company_id: companyId,
        link_id: linkId,
        kind,
        range_days: String(rangeDays),
      });
      const res = await fetch(`/marketing/drilldown?${params}`);
      data = (await res.json()) as DrilldownResponse;
    } catch {
      data = { source, kind, columns: [], rows: [], available: false, unavailable_reason: "marketing.accounts_error", deep_link: "" };
    } finally {
      loading = false;
    }
  }

  // Re-fetch when the range changes (rangeDays is reactive via the key on the parent).
  $effect(() => {
    void rangeDays;
    void load();
  });
</script>

<!-- min-w-0: this root is a grid item (the drill-down grid in MarketingSourceSection); without
     it the item's automatic min-width is the table's min-content width, so a wide table grows
     the page sideways on mobile instead of scrolling inside the overflow-x-auto wrapper below
     (docs/UX.md, "a flex or grid item without min-w-0", #36 / #195). -->
<div class="min-w-0">
  <div class="mb-2 flex items-center justify-between gap-2">
    <h4 class="text-sm font-semibold text-text">{drilldownLabel(kind)}</h4>
    {#if data?.deep_link}
      <a
        href={data.deep_link}
        target="_blank"
        rel="noopener noreferrer"
        class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
      >
        {t("marketing.open_in", { source: sourceLabel(source) })}
        <ExternalLink size={12} />
      </a>
    {/if}
  </div>

  {#if loading}
    <p class="text-sm text-text-muted">{t("marketing.loading")}</p>
  {:else if data && !data.available}
    <p class="text-sm text-text-muted">
      {t("marketing.drilldown_unavailable", { reason: t(data.unavailable_reason ?? "marketing.no_data") })}
    </p>
  {:else if !data || data.rows.length === 0}
    <p class="text-sm text-text-muted">{t("marketing.no_data")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="py-1.5 pr-2 font-medium">{drilldownLabel(kind)}</th>
            {#each data.columns as col (col)}
              <th class="py-1.5 pl-2 text-right font-medium">{metricLabel(col)}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each data.rows as row (row.label)}
            <tr class="border-b border-border/50">
              <td class="max-w-[16rem] truncate py-1.5 pr-2 text-text">
                {#if row.href}
                  <a href={row.href} target="_blank" rel="noopener noreferrer" class="hover:text-brand">
                    {row.label}
                  </a>
                {:else}
                  {row.label}
                {/if}
              </td>
              {#each data.columns as col (col)}
                <td class="py-1.5 pl-2 text-right tabular-nums text-text">
                  {fmtMetric(col, row.metrics[col] ?? 0, currency)}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
