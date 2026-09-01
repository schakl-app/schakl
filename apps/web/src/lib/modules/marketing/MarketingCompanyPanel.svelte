<script lang="ts">
  /**
   * The marketing panel on a client's page (epic #134, key `marketing.overview`).
   *
   * Renders **entirely from stored data** the API panel provider handed down — per linked source a
   * KPI row (last 30 days vs the previous 30) and a sparkline, with a connection-health badge; every
   * number opens the marketing tab (docs/UX.md principle 7). It carries its own edit mode (the
   * contacts-panel pattern): ⋯ → Bewerken reveals removable chips + the account pickers, which post
   * to the host page's `?/marketingLink` / `?/marketingUnlink` actions. Empty states teach.
   */
  import { AlertTriangle, ExternalLink, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import Sparkline from "$lib/core/ui/charts/Sparkline.svelte";
  import EditToggle from "$lib/core/ui/EditToggle.svelte";

  import MarketingSourcePickers from "./MarketingSourcePickers.svelte";
  import {
    comparePeriodLabel,
    deltaClass,
    deltaView,
    fmtMetric,
    healthClass,
    metricHelp,
    metricLabel,
    sourceLabel,
  } from "./format";
  import {
    ALL_SOURCES,
    HEADLINE_METRICS,
    connectHref,
    type CompanyMarketing,
    type MarketingSource,
  } from "./types";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const m = $derived(data as unknown as CompanyMarketing);
  const sources = $derived(m.sources ?? []);
  const canManage = $derived(Boolean(m.can_manage));
  // Key events / conversions are a GA4 concept, so the toggle only appears once GA4 is linked.
  const hasGa4 = $derived(sources.some((s) => s.source === "ga4"));

  let editing = $state(false);
  const tabHref = $derived(`/companies/${companyId}/marketing`);
  // One consent for GA4 + Search Console + Ads together, landing back on this client's page.
  const connect = $derived(connectHref(page.url.pathname + page.url.search));
  // Who a linked source syncs through: "via jou" for your own grant, the colleague's name
  // otherwise. Nobody should have to guess whose account is keeping a client's numbers alive.
  const via = (owner: { name: string; email: string; is_me: boolean } | null | undefined) =>
    owner ? t("marketing.via", { who: owner.is_me ? t("marketing.via_me") : owner.name }) : "";

  // Per-website linking lives in `MarketingSourcePickers` now (#399), which is also what the
  // connect dialog mounts — one copy of "which site does this attach to", so the dialog cannot
  // go on answering it with a hardcoded `false` while this host answers it properly.
  const websites = $derived(m.websites ?? []);

  // Attachments that are not metrics sources (#411) — Tag Manager today. They absorbed the card
  // this hub used to draw beside this one, so `pending_changes` has to be legible *here*,
  // unopened: a change staged weeks ago and never published is how a client's tracking quietly
  // stops being what they were told it is, and deleting the card that said so without moving the
  // number would be deleting the warning.
  const connections = $derived(m.connections ?? []);

  // Google's three, then the two that are not (#300, docs/WORDPRESS.md). Order is display
  // order; the later pickers wrap onto their own row rather than squeezing the others. Shared
  // with the connect dialog (#338) so a sixth source cannot land in one and not the other.
  const linkedIdsBySource = $derived(
    Object.fromEntries(
      ALL_SOURCES.map((s) => [s, sources.filter((x) => x.source === s).map((x) => x.external_id)]),
    ) as Record<MarketingSource, string[]>,
  );

  const headline = (sourceKey: MarketingSource): string[] => HEADLINE_METRICS[sourceKey] ?? [];

  // The panel's deltas are bare percentages with no room for a suffix per tile, so the period
  // they measured against is named once for the panel (#312). Named it must be: this is the
  // first place anyone reads a client's numbers, and "−4%" against an unstated span is the
  // sentence this issue was filed about.
  const comparedPeriod = $derived(m.compare ? comparePeriodLabel(m.compare) : "");
</script>

{#if m.forbidden}
  <!-- Metrics are permission-gated; the panel stays quiet rather than erroring the page. -->
{:else}
  <div class="mb-3 flex items-center justify-between gap-2">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      {#if sources.length > 0}
        <a href={tabHref} class="text-sm font-medium text-brand hover:underline">
          {t("marketing.tab.title")} →
        </a>
        {#if comparedPeriod}
          <span class="text-xs text-text-muted">
            {t("marketing.compare.caption", { period: comparedPeriod })}
          </span>
        {/if}
      {/if}
    </div>
    {#if canManage}
      <EditToggle {editing} onedit={() => (editing = true)} onexit={() => (editing = false)} />
    {/if}
  </div>

  {#if editing}
    <!-- Edit mode: current links as removable chips + the account pickers. -->
    <div class="space-y-4">
      {#if sources.length > 0}
        <ul class="flex flex-wrap gap-2">
          {#each sources as src (src.link_id)}
            <li
              class="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-sm"
            >
              <span class="text-text-muted">{src.label ?? sourceLabel(src.source)}:</span>
              <span class="text-text">{src.display_name}</span>
              {#if src.website_name}
                <span class="text-xs text-text-muted">· {src.website_name}</span>
              {/if}
              {#if src.connection_owner}
                <span class="text-xs text-text-muted" title={src.connection_owner.email}>
                  · {via(src.connection_owner)}
                </span>
              {/if}
              <form method="POST" action="?/marketingUnlink" use:enhance class="flex">
                <input type="hidden" name="link_id" value={src.link_id} />
                <button
                  type="submit"
                  class="ml-0.5 text-text-muted hover:text-red-600"
                  aria-label={t("common.remove")}
                >
                  <X size={14} />
                </button>
              </form>
            </li>
          {/each}
        </ul>
      {/if}
      <!-- The client is the route here, so no `companyId` is posted: `event.params.id` is the
           answer and a form value would be a second one free to disagree with it. -->
      <MarketingSourcePickers {websites} sources={ALL_SOURCES} linkedIds={linkedIdsBySource} />
      {#if hasGa4}
        <!-- Per-client visibility of GA4 key events / conversions (#134); posts to the host
             page's marketingSettings action, which the API gates on marketing.link.manage. -->
        <div class="flex items-center justify-between gap-3 border-t border-border pt-3">
          <div class="min-w-0">
            <p class="text-sm font-medium text-text">{t("marketing.settings.key_events_label")}</p>
            <p class="text-xs text-text-muted">{t("marketing.settings.key_events_hint")}</p>
          </div>
          <form method="POST" action="?/marketingSettings" use:enhance class="inline-flex">
            <input type="hidden" name="show_key_events" value={(!m.show_key_events).toString()} />
            <button
              type="submit"
              role="switch"
              aria-checked={m.show_key_events}
              aria-label={t("marketing.settings.key_events_label")}
              class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors {m.show_key_events
                ? 'bg-brand'
                : 'border border-border bg-surface'}"
            >
              <span
                class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform {m.show_key_events
                  ? 'translate-x-4'
                  : 'translate-x-0.5'}"
              ></span>
            </button>
          </form>
        </div>
      {/if}
    </div>
  {:else if sources.length === 0 && connections.length === 0}
    <!-- One empty state, not two. `needs_connection` is a question about **Google** alone, and it
         used to own a branch of its own that short-circuited everything below it — so an org with
         no Google grant read "koppel een Google-account" over a client whose SE Ranking key and
         Rank Math password were sitting there ready (#399). It decides a *sentence* now, never
         whether the ＋ is offered: the pickers behind it each teach their own missing credential,
         and two of the five have nothing to do with Google. -->
    <div class="rounded-lg border border-dashed border-border p-4 text-sm text-text-muted">
      {#if !canManage}
        <p>{t(m.needs_connection ? "marketing.empty.ask_admin" : "marketing.empty.no_links")}</p>
      {:else}
        <p>
          {t(m.needs_connection ? "marketing.empty.needs_connection" : "marketing.empty.no_links")}
        </p>
        {#if m.needs_connection}
          <a
            href={connect}
            data-sveltekit-preload-data="off"
            class="mt-2 inline-block font-medium text-brand hover:underline"
          >
            {t("marketing.connect_cta")}
          </a>
        {/if}
        <button
          type="button"
          class="mt-2 block font-medium text-brand hover:underline"
          onclick={() => (editing = true)}
        >
          {t("marketing.empty.link_cta")}
        </button>
      {/if}
    </div>
  {:else}
    <div class="space-y-4">
      {#each sources as src (src.link_id)}
        {@const primary = src.series?.metrics?.[src.primary_metric] ?? []}
        <div class="rounded-lg border border-border p-4">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <span class="text-sm font-semibold text-text"
                >{src.label ?? sourceLabel(src.source)}</span
              >
              <span class="truncate text-xs text-text-muted">
                {src.display_name}{#if src.website_name}&nbsp;· {src.website_name}{/if}
              </span>
              <span
                class="rounded-full px-2 py-0.5 text-[10px] font-medium {healthClass(src.health)}"
              >
                {t(`marketing.health.${src.health}`)}
              </span>
              {#if src.connection_owner}
                <span class="text-xs text-text-muted" title={src.connection_owner.email}>
                  {via(src.connection_owner)}
                </span>
              {/if}
            </div>
            {#if src.deep_link}
              <a
                href={src.deep_link}
                target="_blank"
                rel="noopener noreferrer"
                class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
              >
                {t("marketing.open_in", { source: src.label ?? sourceLabel(src.source) })}
                <ExternalLink size={12} />
              </a>
            {/if}
          </div>

          {#if src.health === "pending"}
            <p class="text-sm text-text-muted">{t("marketing.pending_hint")}</p>
          {:else if src.health === "disconnected"}
            <p class="text-sm text-red-600 dark:text-red-400">{t("marketing.disconnected")}</p>
          {:else}
            {#if src.health === "error" && src.last_error}
              <!-- The provider's own sentence, already scrubbed, printed rather than only
                   badged (#411). The Google Ads card this panel absorbed printed it, and it is
                   the one thing that says *what* to fix — an amber "Fout" over four numbers
                   tells a marketeer there is a problem and nothing about whose it is. Shown
                   above the row rather than instead of it: the last numbers that did arrive are
                   still the numbers, and hiding them would make a stale sync look like an
                   empty account. -->
              <p class="mb-2 text-xs text-red-600 dark:text-red-400">{src.last_error}</p>
            {/if}
            <div class="flex flex-wrap items-end gap-4">
              <div class="grid flex-1 grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                {#each headline(src.source) as key (key)}
                  {@const kpi = src.kpis?.[key]}
                  {#if kpi}
                    {@const delta = deltaView(kpi.delta_pct, kpi.lower_is_better)}
                    <!-- The panel is a four-figure summary with a link into the tab that writes
                         the explanations out; a hover is all the room there is here. -->
                    <a href={tabHref} class="group block" title={metricHelp(key) || undefined}>
                      <p class="text-xs text-text-muted">{metricLabel(key)}</p>
                      <p
                        class="text-lg font-semibold tabular-nums text-text group-hover:text-brand"
                      >
                        {fmtMetric(key, kpi.current, src.currency)}
                      </p>
                      {#if delta}
                        <p class="text-xs tabular-nums {deltaClass(delta.tone)}">{delta.text}</p>
                      {/if}
                    </a>
                  {/if}
                {/each}
              </div>
              {#if primary.length > 1}
                <div class="shrink-0">
                  <Sparkline values={primary} />
                </div>
              {/if}
            </div>
          {/if}
        </div>
      {/each}

      {#each connections as conn (conn.id)}
        <!-- A connection, not a source: no KPI row, because there are no numbers and inventing a
             row of them is what would read as broken (#411). One line of facts and the two links
             that act on it — and `pending_changes`, which is the whole reason this row exists on
             the hub rather than only on /marketing/tag-manager. -->
        <div class="rounded-lg border border-border p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <a href={conn.href} class="text-sm font-semibold text-text hover:text-brand">
                {t(`marketing.connection.${conn.kind}`)}
              </a>
              <span class="truncate text-xs text-text-muted">
                {conn.name} · {conn.external_id}
              </span>
              {#if conn.last_error}
                <span
                  class="rounded-full px-2 py-0.5 text-[10px] font-medium {healthClass('error')}"
                  title={conn.last_error}
                >
                  {t("marketing.health.error")}
                </span>
              {/if}
            </div>
            {#if conn.deep_link}
              <a
                href={conn.deep_link}
                target="_blank"
                rel="noopener noreferrer"
                class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
              >
                {t("marketing.open_in", { source: t(`marketing.connection.${conn.kind}`) })}
                <ExternalLink size={12} />
              </a>
            {/if}
          </div>
          <p class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span class="text-text-muted">
              {conn.live_count === 1
                ? t("marketing.connection.live_one")
                : t("marketing.connection.live", { count: conn.live_count })}
            </span>
            {#if conn.pending_changes > 0}
              <!-- The one number the deleted card carried that nothing else did. It leads with a
                   glyph rather than only a colour: on a tenant whose brand is gold, an amber
                   warning and ordinary brand text render identically. -->
              <a
                href={conn.href}
                class="inline-flex items-center gap-1 font-medium text-amber-700 hover:underline dark:text-amber-400"
              >
                <AlertTriangle size={12} aria-hidden="true" />
                {conn.pending_changes === 1
                  ? t("marketing.connection.staged_one")
                  : t("marketing.connection.staged", { count: conn.pending_changes })}
              </a>
            {/if}
          </p>
          {#if conn.last_error}
            <p class="mt-1 text-xs text-red-600 dark:text-red-400">{conn.last_error}</p>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
{/if}
