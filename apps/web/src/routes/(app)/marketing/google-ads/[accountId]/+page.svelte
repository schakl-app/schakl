<script lang="ts">
  /**
   * One Google Ads account: campaigns, keywords, search terms, exclusions and change history.
   *
   * Every table is the same envelope from the API, so the rendering is one component driven by
   * a column list per view rather than five near-identical tables. `warnings` is drawn above
   * the rows and never swallowed: truncation, a shortened change window and provisional recent
   * figures are reported there and nowhere else.
   *
   * The search-terms view is the one that also *writes*. Reviewing a search-terms list is the
   * job this module exists for, and it is two decisions per row rather than one: exclude, or
   * deliberately keep — the second of which leaves no trace in Google and is therefore the half
   * that gets re-proposed forever if it is not written down.
   */
  import { AlertTriangle, Check, Pause, Play } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { pageTitle } from "$lib/core/title";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import GoogleAdsMutationOutcome from "$lib/integrations/google_ads/GoogleAdsMutationOutcome.svelte";
  import GoogleAdsReportTable from "$lib/integrations/google_ads/GoogleAdsReportTable.svelte";
  import GoogleAdsTrend from "$lib/integrations/google_ads/GoogleAdsTrend.svelte";
  import { COLUMNS, type ReportView } from "$lib/integrations/google_ads/columns";
  import { reportFilters } from "$lib/integrations/google_ads/filters";
  import type { GoogleAdsReport, GoogleAdsTrendReport } from "$lib/integrations/google_ads/types";

  let { data, form } = $props();

  const busy = new InFlight();

  const PERIODS = ["30d", "90d", "month", "last_month", "quarter"];

  // Resolved into `$state` rather than awaited in the markup: a raw `{#await}` re-enters its
  // pending branch on every invalidation, so switching period would blank a table that is only
  // being refreshed (the lesson the marketing dashboard already learned).
  // Two genuinely different answers behind one screen: the trend comes from stored rows and
  // carries a compared window, the rest come from Google and carry rows.
  let report = $state<GoogleAdsReport | GoogleAdsTrendReport | null>(null);
  let errorKey = $state<string | null>(null);
  let pending = $state(true);
  $effect(() => {
    const promise = data.report;
    pending = true;
    void promise.then((value) => {
      // A stale resolution loses: clicking two tabs quickly leaves two loads in flight.
      if (data.report !== promise) return;
      report = value.data as GoogleAdsReport | GoogleAdsTrendReport | null;
      errorKey = value.errorKey;
      pending = false;
    });
  });

  // Narrowed once, here, rather than by `data.view` at each use site: the view name and the
  // payload's type are unrelated as far as TypeScript is concerned, so checking one narrows
  // nothing about the other.
  const trend = $derived(data.view === "trend" ? (report as GoogleAdsTrendReport | null) : null);
  const table = $derived(data.view === "trend" ? null : (report as GoogleAdsReport | null));

  const mayReview = $derived(can(page.data.user, "google_ads.negative.write"));
  const mayPause = $derived(can(page.data.user, "google_ads.campaign.write"));
  const reviewing = $derived(data.view === "search-terms" && mayReview);

  /** `{term: "exclude" | "keep"}` — the two decisions, one row at a time. */
  let marks = $state<Record<string, "exclude" | "keep">>({});
  let reason = $state("");
  const marked = $derived(Object.entries(marks));

  // Every selected term must be excluded on one campaign, because that is the level the write
  // happens at. Terms spanning campaigns are refused here rather than silently applied to the
  // first one — a negative on the wrong campaign blocks traffic nobody meant to block.
  const campaigns = $derived(
    new Set(
      marked
        .filter(([, mark]) => mark === "exclude")
        .map(([term]) => String(rowFor(term)?.campaign_id ?? "")),
    ),
  );
  const oneCampaign = $derived(campaigns.size <= 1 ? [...campaigns][0] : null);

  function rowFor(term: string): Record<string, unknown> | undefined {
    return table?.rows.find((row) => String(row.search_term) === term);
  }

  function mark(term: string, value: "exclude" | "keep"): void {
    marks =
      marks[term] === value
        ? Object.fromEntries(Object.entries(marks).filter(([key]) => key !== term))
        : { ...marks, [term]: value };
  }

  /**
   * A tab or period link, built from scratch rather than from the current URL.
   *
   * Which is how it drops the page, the search and the status in one move — and it must. Page 4
   * of the campaigns list is not page 4 of the search terms, a status of PAUSED means nothing on
   * a change history, and a search for "dakraam" carried into the negatives tab would open it
   * looking empty for a reason nothing on the screen explains (`core/table/paging.ts`).
   */
  function href(view: string, period: string): string {
    const params = new URLSearchParams();
    if (view !== "campaigns") params.set("view", view);
    if (period !== "30d") params.set("period", period);
    const qs = params.toString();
    return `/marketing/google-ads/${page.params.accountId}${qs ? `?${qs}` : ""}`;
  }

  // No filter bar over the trend: it is a summary of one period against another, not a list, so
  // there is nothing on it to narrow and nothing to page.
  const filters = $derived(
    data.view === "trend"
      ? []
      : // An unlabelled amount, never a guessed symbol: the ladder says "vanaf 10" rather than
        // "vanaf € 10" for an account whose currency we have not read yet.
        reportFilters(data.view as ReportView, data.account.currency_code ?? null),
  );

  /** Persist the chosen size as this view's default. The URL stays the current view. */
  function rememberSize(size: number): void {
    const body = new FormData();
    body.set("view", data.view);
    body.set("page_size", String(size));
    void fetch("?/saveTable", {
      method: "POST",
      headers: { "x-sveltekit-action": "true" },
      body,
    });
  }

  /** What has already been decided about this term, from the API's own annotation. */
  function decided(row: Record<string, unknown>): { decision: string; reason: string } | null {
    return (row.decided ?? null) as { decision: string; reason: string } | null;
  }
</script>

<svelte:head>
  <title>{pageTitle(data.account.descriptive_name)}</title>
</svelte:head>

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

{#if filters.length > 0}
  <FilterBar {filters} idPrefix="google-ads-report" />
{/if}

{#if form?.outcome}
  <GoogleAdsMutationOutcome outcome={form.outcome} />
{:else if form?.key}
  <p class="mb-3 rounded-xl border border-border bg-surface-raised p-3 text-sm text-text">
    {t(form.key)}
  </p>
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
      {#if trend}
        <!-- The compared span is *named*, never left as "vs. previous period": a comparison set
             to the wrong thing otherwise looks exactly like one set to the right thing (#312). -->
        · {t("google_ads.trend.compared_with", {
          from: trend.compared_with.date_from,
          to: trend.compared_with.date_to,
        })}
      {/if}
    </p>
  {/if}

  {#if trend}
    <GoogleAdsTrend
      totals={trend.totals}
      previous={trend.previous_totals}
      change={trend.change ?? {}}
      breakdown={trend.breakdown ?? []}
      currency={trend.currency}
    />
  {:else if reviewing && table}
    <!--
      The review pass. A term is marked exclude or keep — and neither is the default, because a
      blank third state is the honest one: most terms on a list are simply not worth a decision
      yet, and forcing one would fill the log with judgements nobody made.
    -->
    <form
      method="POST"
      action="?/review"
      use:enhance={busy.clear("review")}
      class="mb-3 flex flex-wrap items-end gap-3 rounded-xl border border-border bg-surface-raised p-3"
    >
      <input type="hidden" name="campaign_id" value={oneCampaign ?? ""} />
      {#each marked as [term, value] (term)}
        <input type="hidden" name={value} value={term} />
      {/each}
      <label class="flex-1 text-sm">
        <span class="mb-1 block text-xs font-medium text-text-muted"
          >{t("google_ads.review.reason")}</span
        >
        <input
          name="reason"
          bind:value={reason}
          placeholder={t("google_ads.review.reason_hint")}
          class="w-full rounded-lg border border-border bg-surface px-3 py-1.5 text-sm"
        />
      </label>
      <button
        type="submit"
        disabled={marked.length === 0 || campaigns.size > 1}
        class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {t("google_ads.review.submit", {
          exclude: marked.filter(([, m]) => m === "exclude").length,
          keep: marked.filter(([, m]) => m === "keep").length,
        })}
      </button>
      {#if campaigns.size > 1}
        <!-- A negative is written on one campaign. Refusing here beats picking one silently:
             an exclusion on the wrong campaign blocks traffic nobody meant to block. -->
        <p class="w-full text-xs text-text-muted">{t("google_ads.review.one_campaign_only")}</p>
      {/if}
    </form>

    <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
      <table class="w-full min-w-max text-sm">
        <thead>
          <tr class="border-b border-border text-left">
            <th class="px-3 py-2 text-xs font-medium text-text-muted"
              >{t("google_ads.column.search_term")}</th
            >
            <th class="px-3 py-2 text-xs font-medium text-text-muted"
              >{t("google_ads.column.decided")}</th
            >
            <th class="px-3 py-2 text-right text-xs font-medium text-text-muted"
              >{t("google_ads.metric.cost")}</th
            >
            <th class="px-3 py-2 text-right text-xs font-medium text-text-muted"
              >{t("google_ads.metric.clicks")}</th
            >
            <th class="px-3 py-2 text-right text-xs font-medium text-text-muted"
              >{t("google_ads.metric.conversions")}</th
            >
            <th class="px-3 py-2 text-xs font-medium text-text-muted"
              >{t("google_ads.review.decide")}</th
            >
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each table.rows as row (String(row.search_term))}
            {@const term = String(row.search_term)}
            {@const already = decided(row)}
            <tr>
              <td class="px-3 py-2">{term}</td>
              <td class="px-3 py-2 text-xs text-text-muted">
                {#if already}
                  <!-- State carried by a word, never by a colour: `text-brand` is gold on some
                       tenants and reads identically to an amber warning. -->
                  {t(`google_ads.decision.${already.decision}`)}{#if already.reason}
                    · {already.reason}{/if}
                {:else}
                  –
                {/if}
              </td>
              <td class="px-3 py-2 text-right tabular-nums">{row.cost}</td>
              <td class="px-3 py-2 text-right tabular-nums">{row.clicks}</td>
              <td class="px-3 py-2 text-right tabular-nums">{row.conversions}</td>
              <td class="px-3 py-2">
                <div class="flex gap-1">
                  <button
                    type="button"
                    onclick={() => mark(term, "exclude")}
                    aria-pressed={marks[term] === "exclude"}
                    class="rounded-lg px-2 py-1 text-xs font-medium {marks[term] === 'exclude'
                      ? 'bg-text text-surface'
                      : 'text-text-muted hover:bg-surface'}"
                  >
                    {t("google_ads.review.exclude")}
                  </button>
                  <button
                    type="button"
                    onclick={() => mark(term, "keep")}
                    aria-pressed={marks[term] === "keep"}
                    class="rounded-lg px-2 py-1 text-xs font-medium {marks[term] === 'keep'
                      ? 'bg-text text-surface'
                      : 'text-text-muted hover:bg-surface'}"
                  >
                    <Check size={12} class="mr-1 inline" aria-hidden="true" />{t(
                      "google_ads.review.keep",
                    )}
                  </button>
                </div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if table && data.view === "campaigns" && mayPause}
    <GoogleAdsReportTable
      columns={COLUMNS[data.view as ReportView]}
      rows={table.rows}
      totals={table.totals}
      currency={table.currency}
    />
    <!--
      Pause and resume, one campaign at a time. Deliberately the only campaign write on this
      screen: creating a campaign needs a budget, an ad group, keywords and an ad before it does
      anything, and a browser wizard for that is not what this module is for — the MCP surface is.
    -->
    <div class="mt-3 flex flex-wrap gap-2">
      {#each table.rows.filter((row) => row.status === "ENABLED" || row.status === "PAUSED") as row (String(row.campaign_id))}
        <form method="POST" action="?/campaign_status" use:enhance={busy.clear("campaign")}>
          <input type="hidden" name="campaign_id" value={String(row.campaign_id)} />
          <input
            type="hidden"
            name="status"
            value={row.status === "ENABLED" ? "PAUSED" : "ENABLED"}
          />
          <button
            type="submit"
            class="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:bg-surface"
          >
            {#if row.status === "ENABLED"}
              <Pause size={12} aria-hidden="true" />
            {:else}
              <Play size={12} aria-hidden="true" />
            {/if}
            {row.campaign_name}
          </button>
        </form>
      {/each}
    </div>
  {:else if table}
    <GoogleAdsReportTable
      columns={COLUMNS[data.view as ReportView]}
      rows={table.rows}
      totals={table.totals}
      currency={table.currency}
    />
  {/if}

  {#if table}
    <!--
      `total_rows`, never `rows.length`. The rows on screen are one page; the pager's whole job is
      to say what they are a page *of*, and a count taken from them reads "1 tot 50 van 50" on
      every page of a list of nine hundred.
    -->
    <Pagination
      total={table.total_rows}
      page={data.paging.page}
      limit={data.paging.limit}
      onsize={rememberSize}
    />
  {/if}
{/if}
