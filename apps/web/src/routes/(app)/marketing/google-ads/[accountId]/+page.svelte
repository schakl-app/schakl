<script lang="ts">
  /**
   * One Google Ads account: campaigns, keywords, search terms, exclusions and change history.
   *
   * Every table is the same envelope from the API, so the rendering is one component driven by
   * a column list per view rather than five near-identical tables. `warnings` is drawn above
   * the rows and never swallowed: truncation, a shortened change window and provisional recent
   * figures are reported there and nowhere else.
   */
  import { AlertTriangle } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import GoogleAdsReportTable from "$lib/modules/google_ads/GoogleAdsReportTable.svelte";
  import { COLUMNS, type ReportView } from "$lib/modules/google_ads/columns";
  import type { GoogleAdsReport } from "$lib/modules/google_ads/types";

  let { data } = $props();

  const VIEWS: ReportView[] = ["campaigns", "keywords", "search-terms", "negatives", "changes"];
  const PERIODS = ["30d", "90d", "month", "last_month", "quarter"];

  // Resolved into `$state` rather than awaited in the markup: a raw `{#await}` re-enters its
  // pending branch on every invalidation, so switching period would blank a table that is only
  // being refreshed (the lesson the marketing dashboard already learned).
  let report = $state<GoogleAdsReport | null>(null);
  let errorKey = $state<string | null>(null);
  let pending = $state(true);
  $effect(() => {
    const promise = data.report;
    pending = true;
    void promise.then((value) => {
      // A stale resolution loses: clicking two tabs quickly leaves two loads in flight.
      if (data.report !== promise) return;
      report = value.data as GoogleAdsReport | null;
      errorKey = value.errorKey;
      pending = false;
    });
  });

  function href(view: string, period: string): string {
    const params = new URLSearchParams();
    if (view !== "campaigns") params.set("view", view);
    if (period !== "30d") params.set("period", period);
    const qs = params.toString();
    return `/marketing/google-ads/${page.params.accountId}${qs ? `?${qs}` : ""}`;
  }

  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<svelte:head>
  <title>{pageTitle(data.account.descriptive_name)}</title>
</svelte:head>

<!-- Sub-route tabs at the very top of the section, above the heading (docs/UX.md, Navigation). -->
<nav class="mb-4 flex flex-wrap gap-1" aria-label={t("google_ads.nav.reports")}>
  {#each VIEWS as view (view)}
    <a href={href(view, data.period)} class={tabClass(data.view === view)}>
      {t(`google_ads.view.${view.replace("-", "_")}`)}
    </a>
  {/each}
</nav>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{data.account.descriptive_name}</h1>
  <p class="mt-1 text-sm text-text-muted">
    {data.account.customer_id_formatted}
    {#if data.account.currency_code}· {data.account.currency_code}{/if}
    {#if data.account.time_zone}· {data.account.time_zone}{/if}
  </p>
</div>

{#if data.view !== "negatives"}
  <nav class="mb-4 flex flex-wrap gap-1" aria-label={t("google_ads.nav.period")}>
    {#each PERIODS as period (period)}
      <a
        href={href(data.view, period)}
        class="rounded-lg px-2.5 py-1 text-xs font-medium {data.period === period
          ? 'bg-surface text-text'
          : 'text-text-muted hover:bg-surface'}"
      >
        {t(`google_ads.period.${period}`)}
      </a>
    {/each}
  </nav>
{/if}

{#if pending}
  <p class="text-sm text-text-muted">{t("google_ads.loading")}</p>
{:else if errorKey}
  <!-- A refused Google call is a state this screen draws, not a 500. The key says which of the
       several very different problems it is: reconnect, an unapproved developer token, a
       suspended account, a sunset API version. -->
  <div class="flex items-start gap-2 rounded-xl border border-border bg-surface-raised p-4">
    <AlertTriangle size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
    <p class="text-sm text-text">{t(errorKey)}</p>
  </div>
{:else if report}
  {#if report.warnings.length > 0}
    <!-- Never swallowed: a capped list that says nothing reads as a complete one. -->
    <ul class="mb-3 space-y-1">
      {#each report.warnings as warning (warning)}
        <li class="flex items-start gap-1.5 text-xs text-text-muted">
          <AlertTriangle size={12} class="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{t(warning)}</span>
        </li>
      {/each}
    </ul>
  {/if}

  {#if report.period}
    <p class="mb-2 text-xs text-text-muted">
      {t("google_ads.period.range", {
        from: report.period.date_from,
        to: report.period.date_to,
      })}
    </p>
  {/if}

  <GoogleAdsReportTable
    columns={COLUMNS[data.view]}
    rows={report.rows}
    totals={report.totals}
    currency={report.currency}
  />
{/if}
