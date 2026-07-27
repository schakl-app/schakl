<script lang="ts">
  /**
   * My Day widget (#254): the top linked clients' headline marketing KPI (GA4 sessions, else
   * GSC clicks), each row opening that client's marketing tab. A teaser for the existing
   * marketing pages — never a second marketing UI. The API caps the rows and reports the real
   * linked count, so the card says "top n of this" instead of implying the cap is everything.
   */
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  import { deltaClass, deltaView, fmtMetric, metricLabel } from "./format";

  let { data }: { data: unknown } = $props();

  interface Kpi {
    current: number;
    previous: number;
    delta_pct: number | null;
    lower_is_better: boolean;
  }
  interface Row {
    company_id: string;
    company_name: string;
    metric: string;
    kpi: Kpi;
  }
  interface Summary {
    range_days: number;
    linked_total: number;
    rows: Row[];
  }
  const summary = $derived((data ?? { range_days: 30, linked_total: 0, rows: [] }) as Summary);
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.marketing.summary")}
  href="/marketing"
  linkLabel={t("nav.marketing")}
>
  {#if summary.linked_total === 0}
    <p class="text-sm text-text-muted">{t("marketing.empty.no_links")}</p>
  {:else}
    <ul class="space-y-2">
      {#each summary.rows as row (row.company_id)}
        {@const delta = deltaView(row.kpi.delta_pct, row.kpi.lower_is_better)}
        <li>
          <a
            href={`/companies/${row.company_id}/marketing`}
            class="group flex items-baseline justify-between gap-3"
          >
            <span class="min-w-0 truncate text-sm text-text group-hover:text-brand">
              {row.company_name}
            </span>
            <span class="flex shrink-0 items-baseline gap-2 text-sm tabular-nums">
              <span class="font-semibold text-text">
                {fmtMetric(row.metric, row.kpi.current)}
                <span class="font-normal text-text-muted">{metricLabel(row.metric)}</span>
              </span>
              {#if delta}
                <span class="text-xs {deltaClass(delta.tone)}">{delta.text}</span>
              {/if}
            </span>
          </a>
        </li>
      {/each}
    </ul>
    {#if summary.linked_total > summary.rows.length}
      <p class="mt-2 text-xs text-text-muted">
        {t("marketing.widget.more", {
          shown: summary.rows.length,
          total: summary.linked_total,
        })}
      </p>
    {/if}
  {/if}
</DashboardWidgetCard>
