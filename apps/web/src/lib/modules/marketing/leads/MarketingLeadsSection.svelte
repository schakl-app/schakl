<script lang="ts">
  /**
   * The leads dashboard for one client (docs/MARKETING.md): the form, call and e-mail
   * conversions GA4 measures, and the ad spend behind them — laid out as the fixed report a
   * Looker Studio page used to be, built from the client's measurement profile.
   *
   * Two parts, each drawn only when its source answered: **Leads** (GA4) and **Advertenties**
   * (Google Ads). Every widget is one of eight shapes over rows the API already labelled; the
   * filter chips and a click on a category narrow the whole page through the URL (§9, the URL
   * is the view), so a narrowed dashboard is a link. Warnings, coverage and the fixed note are
   * the honesty strip (spec §8, §10): a sampled number, a `(not set)` share and a measurement
   * breakpoint are each a sentence beside the numbers, never a footnote nobody finds.
   */
  import { ExternalLink, Settings2, X } from "@lucide/svelte";

  import { fmtDateTime, fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { breakpointLabel, fmtUnit, unavailableReason } from "./format";
  import LeadWidget from "./LeadWidget.svelte";
  import type { LeadsDashboard, LeadWidget as Widget } from "./types";

  let {
    leads,
    pending = false,
    errorKey = null,
    activeFilters = {},
    filterHref,
    clearFiltersHref,
    periodHref,
    profileHref = null,
    isPortal = false,
  }: {
    leads: LeadsDashboard | null;
    pending?: boolean;
    errorKey?: string | null;
    /** `{dimension: [values]}` as read from the URL. */
    activeFilters?: Record<string, string[]>;
    /** A link that toggles one value of one dimension in the page's filters. */
    filterHref: (dimension: string, key: string) => string;
    clearFiltersHref: string;
    /** A link to this page on another period token (the "from the breakpoint" offer). */
    periodHref: (token: string) => string;
    /** Where the measurement profile is edited; `null` for a reader who may not. */
    profileHref?: string | null;
    isPortal?: boolean;
  } = $props();

  const scorecards = (part: "leads" | "ads"): Widget[] =>
    (leads?.widgets ?? []).filter((w) => w.part === part && w.kind === "scorecard");
  const others = (part: "leads" | "ads"): Widget[] =>
    (leads?.widgets ?? []).filter((w) => w.part === part && w.kind !== "scorecard");
  const hasFilters = $derived(Object.values(activeFilters).some((v) => v.length > 0));
  const wide = new Set([
    "requests_by_channel",
    "service_by_channel",
    "requests_by_page",
    "ads_campaigns",
    "ads_conversion_actions",
    "funnel",
    "ads_by_day",
  ]);

  function warningText(code: string, details: Record<string, unknown>): string {
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(details)) params[k] = v == null ? "" : String(v);
    if (code === "before_breakpoint" && details.date)
      params.date = fmtDayMonthYear(String(details.date));
    // A refusal travels as an i18n key (§9), so the sentence is ours in the reader's language.
    if (typeof details.reason === "string" && details.reason.includes(".")) {
      params.reason = t(details.reason);
    }
    return t(`marketing.leads.warning.${code}`, params);
  }
</script>

{#if pending && !leads}
  <div
    class="rounded-xl border border-dashed border-border bg-surface-raised p-6 text-center text-sm text-text-muted"
  >
    {t("marketing.leads.loading")}
  </div>
{:else if errorKey && !leads}
  <div
    class="rounded-xl border border-dashed border-border bg-surface-raised p-6 text-center text-sm text-text-muted"
  >
    {t(errorKey)}
  </div>
{:else if leads && !leads.configured}
  {#if !isPortal}
    <div class="rounded-xl border border-dashed border-border bg-surface-raised p-6 text-center">
      <p class="text-sm text-text-muted">{t("marketing.leads.not_configured")}</p>
      {#if profileHref}
        <a
          href={profileHref}
          class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
        >
          {t("marketing.leads.profile.create")}
        </a>
      {/if}
    </div>
  {/if}
{:else if leads}
  <section class="space-y-5">
    <!-- The honesty strip: what the numbers cannot say for themselves. -->
    {#if leads.warnings.length}
      <ul class="space-y-1.5">
        {#each leads.warnings as warning (warning.code + JSON.stringify(warning.details))}
          <li
            class="rounded-lg border px-3 py-2 text-sm {warning.severity === 'error'
              ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200'
              : warning.severity === 'warning'
                ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100'
                : 'border-border bg-surface text-text-muted'}"
          >
            {warningText(warning.code, warning.details)}
            {#if warning.code === "before_breakpoint" && leads.window?.comparable_from}
              <a
                href={periodHref(`${leads.window.comparable_from}..${leads.window.end}`)}
                data-sveltekit-noscroll
                class="ml-1 font-medium underline"
              >
                {t("marketing.leads.warning.from_breakpoint")}
              </a>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    <!-- Filters: every dimension the profile marked filterable, with the values this period saw. -->
    {#if leads.filters.length}
      <div class="flex flex-wrap items-center gap-2">
        {#each leads.filters as filter (filter.dimension)}
          <div class="flex flex-wrap items-center gap-1 rounded-lg border border-border px-2 py-1">
            <span class="text-xs text-text-muted">
              {filter.title ?? t(`marketing.leads.dimension.${filter.dimension}`)}
            </span>
            {#each filter.options as option (option.key)}
              {@const active = filter.active.includes(option.key)}
              <a
                href={filterHref(filter.dimension, option.key)}
                data-sveltekit-noscroll
                class="rounded px-1.5 py-0.5 text-xs {active
                  ? 'bg-brand text-white'
                  : 'text-text hover:bg-surface'}"
                aria-current={active ? "true" : undefined}
              >
                {option.label}
                {#if option.count}<span class="opacity-70">·{fmtUnit(option.count, "count")}</span
                  >{/if}
              </a>
            {/each}
          </div>
        {/each}
        {#if hasFilters}
          <a
            href={clearFiltersHref}
            data-sveltekit-noscroll
            class="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-muted hover:text-text"
          >
            <X size={12} />
            {t("marketing.leads.filter.clear")}
          </a>
        {/if}
      </div>
    {/if}

    {#if leads.ga4_available}
      <div>
        <div class="mb-2 flex items-center justify-between gap-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-text-muted">
            {t("marketing.leads.part.leads")}
          </h2>
          {#if leads.ga4_deep_link}
            <a
              href={leads.ga4_deep_link}
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
            >
              GA4 <ExternalLink size={12} />
            </a>
          {/if}
        </div>
        {#if scorecards("leads").length}
          <div class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {#each scorecards("leads") as widget (widget.key)}
              <LeadWidget {widget} />
            {/each}
          </div>
        {/if}
        <div class="grid gap-3 md:grid-cols-2">
          {#each others("leads") as widget (widget.key)}
            <div class={wide.has(widget.key) ? "md:col-span-2" : ""}>
              <LeadWidget {widget} {activeFilters} {filterHref} breakpoints={leads.breakpoints} />
            </div>
          {/each}
        </div>
      </div>
    {/if}

    {#if leads.ads_available}
      <div>
        <div class="mb-2 flex items-center justify-between gap-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-text-muted">
            {t("marketing.leads.part.ads")}
          </h2>
          {#if leads.ads_deep_link}
            <a
              href={leads.ads_deep_link}
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
            >
              Google Ads <ExternalLink size={12} />
            </a>
          {/if}
        </div>
        {#if scorecards("ads").length}
          <div class="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {#each scorecards("ads") as widget (widget.key)}
              <LeadWidget {widget} />
            {/each}
          </div>
        {/if}
        <div class="grid gap-3 md:grid-cols-2">
          {#each others("ads") as widget (widget.key)}
            <div class={wide.has(widget.key) ? "md:col-span-2" : ""}>
              <LeadWidget {widget} />
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Coverage: the (not set) share per dimension, always visible (spec §10). -->
    {#if leads.coverage.length}
      <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        <span>{t("marketing.leads.coverage.title")}</span>
        {#each leads.coverage as cov (cov.dimension)}
          <span class={cov.share > 0.1 ? "text-amber-700 dark:text-amber-400" : ""}>
            {cov.title ?? t(`marketing.leads.dimension.${cov.dimension}`)}:
            {fmtUnit(1 - cov.share, "ratio")}
          </span>
        {/each}
      </div>
    {/if}

    <!-- The fixed note: the breakpoints, what a request is and is not, the tenant's sentence. -->
    <div
      class="rounded-lg border border-border bg-surface px-3 py-2 text-xs leading-relaxed text-text-muted"
    >
      {#each leads.breakpoints as bp (bp.date)}
        <p>
          {t(`marketing.leads.note.breakpoint_${bp.severity}`, {
            date: breakpointLabel(bp.date, bp.text),
          })}
        </p>
      {/each}
      <p>{t("marketing.leads.note.requests_not_orders")}</p>
      {#if leads.disclaimer}<p>{leads.disclaimer}</p>{/if}
      <p class="mt-1 flex flex-wrap items-center gap-x-3">
        {#if leads.refreshed_at}
          <span>{t("marketing.leads.refreshed_at", { when: fmtDateTime(leads.refreshed_at) })}</span
          >
        {/if}
        {#if profileHref}
          <a href={profileHref} class="flex items-center gap-1 text-brand hover:underline">
            <Settings2 size={12} />
            {t("marketing.leads.profile.edit")}
          </a>
        {/if}
      </p>
    </div>

    {#if leads.unavailable.length && !isPortal}
      <details class="text-xs text-text-muted">
        <summary class="cursor-pointer">
          {t("marketing.leads.unavailable.title", { count: String(leads.unavailable.length) })}
        </summary>
        <ul class="mt-1 space-y-0.5">
          {#each leads.unavailable as item (item.key)}
            <li>{t(`marketing.leads.widget.${item.key}`)} — {unavailableReason(item.reason)}</li>
          {/each}
        </ul>
      </details>
    {/if}
  </section>
{/if}
