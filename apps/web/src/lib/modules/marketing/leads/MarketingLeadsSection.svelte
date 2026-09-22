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
   *
   * **Each part is a section, drawn the way the per-source sections below it are**: a white
   * card on the page's ground, figures as tinted `stat` tiles, lists and charts as outlined
   * boxes inside it (docs/UX.md, the visual system). The first version drew every tile as a
   * hairline box with no fill, directly on the page — tiles the colour of the background, under
   * a 12 px uppercase muted heading, which is the quietest treatment the system has.
   */
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import ListFilter from "@lucide/svelte/icons/list-filter";
  import Settings2 from "@lucide/svelte/icons/settings-2";
  import X from "@lucide/svelte/icons/x";

  import { fmtDateTime, fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { BAND_HEADING, FIELD_LABEL } from "$lib/core/ui/headings";
  import Spinner from "$lib/core/ui/Spinner.svelte";

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
  // A narrowed read is Google's latency (seconds), and the previous answer stays on screen
  // while it runs. Without saying so, a click on a filter looked like a click that did nothing.
  const updating = $derived(pending && leads !== null);

  // The widgets that need the full width: many columns, or a time axis.
  const WIDE = new Set([
    "service_by_channel",
    "requests_by_page",
    "ads_campaigns",
    "ads_conversion_actions",
    "funnel",
    "ads_by_day",
  ]);

  /**
   * Two columns with no holes. Which widgets exist depends on the client's profile, so a fixed
   * list of spans left a half-width widget alone in its row wherever a wide one followed it —
   * a block-sized gap, three of them on one real dashboard. A half with no half beside it takes
   * the row; computed over the ordered list, so nothing is reordered to fill a gap.
   */
  function arranged(list: Widget[]): { widget: Widget; wide: boolean }[] {
    const out: { widget: Widget; wide: boolean }[] = [];
    for (let i = 0; i < list.length; i++) {
      const widget = list[i];
      const next = list[i + 1];
      if (WIDE.has(widget.key)) out.push({ widget, wide: true });
      else if (next && !WIDE.has(next.key)) {
        out.push({ widget, wide: false }, { widget: next, wide: false });
        i++;
      } else out.push({ widget, wide: true });
    }
    return out;
  }

  /**
   * The filter controls, with what is *picked* read from the URL rather than from the payload:
   * the payload describes the previous view until the new one lands, and a chip that lights up
   * two seconds after it was pressed reads as a press that missed. A picked value the payload
   * does not list yet (a page title clicked in a table) gets a chip of its own at once.
   */
  type Chip = { key: string; label: string; count: number; active: boolean };
  type Group = { dimension: string; title: string; chips: Chip[] };
  const groups = $derived.by((): Group[] => {
    const out: Group[] = [];
    const listed: string[] = [];
    for (const filter of leads?.filters ?? []) {
      listed.push(filter.dimension);
      const picked = activeFilters[filter.dimension] ?? [];
      const chips: Chip[] = filter.options.map((o) => ({
        key: o.key,
        label: o.label,
        count: o.count,
        active: picked.includes(o.key),
      }));
      for (const key of picked) {
        if (!chips.some((c) => c.key === key))
          chips.push({ key, label: key, count: 0, active: true });
      }
      if (chips.length === 0) continue;
      out.push({
        dimension: filter.dimension,
        title: filter.title ?? t(`marketing.leads.dimension.${filter.dimension}`),
        chips,
      });
    }
    for (const [dimension, picked] of Object.entries(activeFilters)) {
      if (listed.includes(dimension) || picked.length === 0) continue;
      out.push({
        dimension,
        title: t(`marketing.leads.dimension.${dimension}`),
        chips: picked.map((key) => ({ key, label: key, count: 0, active: true })),
      });
    }
    return out;
  });

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
  <!-- A cold read is Google's latency, so what stands in for it has the shape of what it
       becomes — a heading, a row of tiles, a chart — rather than one line that the whole page
       below then moves down to make room for (docs/UX.md: a placeholder reserves the space). -->
  <section
    class="rounded-xl border border-border bg-surface-raised p-4 sm:p-5"
    aria-busy="true"
    role="status"
  >
    <div class="mb-4 h-5 w-40 animate-pulse rounded bg-surface"></div>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {#each [0, 1, 2, 3] as i (i)}
        <div class="h-20 animate-pulse rounded-lg bg-surface"></div>
      {/each}
    </div>
    <div class="mt-4 h-56 animate-pulse rounded-lg bg-surface"></div>
    <p class="mt-3 text-xs text-text-muted">{t("marketing.leads.loading")}</p>
  </section>
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
  <section class="space-y-4" aria-busy={updating}>
    <!-- The honesty strip: what the numbers cannot say for themselves. -->
    {#if leads.warnings.length}
      <ul class="space-y-1.5">
        {#each leads.warnings as warning (warning.code + JSON.stringify(warning.details))}
          <li
            class="rounded-lg border px-3 py-2 text-sm {warning.severity === 'error'
              ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200'
              : warning.severity === 'warning'
                ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100'
                : 'border-border bg-surface-raised text-text-muted'}"
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

    {#if leads.ga4_available}
      <section class="rounded-xl border border-border bg-surface-raised p-4 sm:p-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h2 class={BAND_HEADING}>{t("marketing.leads.part.leads")}</h2>
          <span class="flex items-center gap-3">
            {#if updating}
              <span class="flex items-center gap-1.5 text-xs text-text-muted" role="status">
                <Spinner size={12} />
                {t("marketing.leads.updating")}
              </span>
            {/if}
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
          </span>
        </div>

        <!-- The filters belong to this part and sit inside it: they narrow what GA4 measured,
             and the advertising figures below are never joined to them (docs/MARKETING.md). One
             labelled row per dimension, every value the period saw, the picked ones filled and
             carrying their own ✕ — so a second value can be added, or another switched to,
             without clearing first. -->
        {#if groups.length}
          <div class="mb-4 rounded-lg bg-surface-tint p-3">
            <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p class="flex items-center gap-1.5 {FIELD_LABEL}">
                <ListFilter size={13} aria-hidden="true" />
                {t("marketing.leads.filter.title")}
                <span class="hidden font-normal sm:inline"
                  >· {t("marketing.leads.filter.hint")}</span
                >
              </p>
              {#if hasFilters}
                <a
                  href={clearFiltersHref}
                  data-sveltekit-noscroll
                  class="flex items-center gap-1 text-xs font-medium text-brand hover:underline"
                >
                  <X size={12} aria-hidden="true" />
                  {t("marketing.leads.filter.clear")}
                </a>
              {/if}
            </div>
            <dl class="space-y-1.5">
              {#each groups as group (group.dimension)}
                <div class="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-3">
                  <dt class="shrink-0 text-xs text-text-muted sm:w-32">{group.title}</dt>
                  <dd class="flex flex-wrap gap-1.5">
                    {#each group.chips as chip (chip.key)}
                      <a
                        href={filterHref(group.dimension, chip.key)}
                        data-sveltekit-noscroll
                        class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors {chip.active
                          ? 'border-brand bg-brand font-medium text-white'
                          : 'border-border bg-surface-raised text-text hover:border-brand hover:text-brand'}"
                        aria-current={chip.active ? "true" : undefined}
                        aria-label={chip.active
                          ? t("marketing.leads.filter.remove", { value: chip.label })
                          : undefined}
                      >
                        {chip.label}
                        {#if chip.count}
                          <span
                            class="tabular-nums {chip.active ? 'opacity-80' : 'text-text-muted'}"
                          >
                            {fmtUnit(chip.count, "count")}
                          </span>
                        {/if}
                        {#if chip.active}<X size={12} aria-hidden="true" />{/if}
                      </a>
                    {/each}
                  </dd>
                </div>
              {/each}
            </dl>
          </div>
        {/if}

        <div class="transition-opacity {updating ? 'opacity-50' : ''}">
          {#if scorecards("leads").length}
            <div
              class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]"
            >
              {#each scorecards("leads") as widget (widget.key)}
                <LeadWidget {widget} />
              {/each}
            </div>
          {/if}
          <div class="grid gap-3 md:grid-cols-2">
            {#each arranged(others("leads")) as { widget, wide } (widget.key)}
              <div class={wide ? "md:col-span-2" : ""}>
                <LeadWidget {widget} {activeFilters} {filterHref} breakpoints={leads.breakpoints} />
              </div>
            {/each}
          </div>
        </div>
      </section>
    {/if}

    {#if leads.ads_available}
      <section class="rounded-xl border border-border bg-surface-raised p-4 sm:p-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h2 class={BAND_HEADING}>{t("marketing.leads.part.ads")}</h2>
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
          <div
            class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]"
          >
            {#each scorecards("ads") as widget (widget.key)}
              <LeadWidget {widget} />
            {/each}
          </div>
        {/if}
        <div class="grid gap-3 md:grid-cols-2">
          {#each arranged(others("ads")) as { widget, wide } (widget.key)}
            <div class={wide ? "md:col-span-2" : ""}>
              <LeadWidget {widget} />
            </div>
          {/each}
        </div>
      </section>
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
      class="rounded-lg border border-border bg-surface-raised px-3 py-2 text-xs leading-relaxed text-text-muted"
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
