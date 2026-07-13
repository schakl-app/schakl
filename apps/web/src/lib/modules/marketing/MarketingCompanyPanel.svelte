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
  import { Pencil, Check, ExternalLink, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Sparkline from "$lib/core/ui/charts/Sparkline.svelte";

  import MarketingAccountPicker from "./MarketingAccountPicker.svelte";
  import { deltaClass, deltaView, fmtMetric, healthClass, metricLabel, sourceLabel } from "./format";
  import { HEADLINE_METRICS, connectHref, type CompanyMarketing, type MarketingSource } from "./types";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const m = $derived(data as unknown as CompanyMarketing);
  const sources = $derived(m.sources ?? []);
  const canManage = $derived(Boolean(m.can_manage));

  let editing = $state(false);
  const tabHref = $derived(`/companies/${companyId}/marketing`);

  const SOURCE_ORDER: MarketingSource[] = ["ga4", "gsc", "gads"];
  const linkedIdsBySource = $derived(
    Object.fromEntries(
      SOURCE_ORDER.map((s) => [s, sources.filter((x) => x.source === s).map((x) => x.external_id)]),
    ) as Record<MarketingSource, string[]>,
  );

  const headline = (sourceKey: MarketingSource): string[] => HEADLINE_METRICS[sourceKey] ?? [];
</script>

{#if m.forbidden}
  <!-- Metrics are permission-gated; the panel stays quiet rather than erroring the page. -->
{:else}
  <div class="mb-3 flex items-center justify-between gap-2">
    <div class="flex items-center gap-3">
      {#if sources.length > 0}
        <a href={tabHref} class="text-sm font-medium text-brand hover:underline">
          {t("marketing.tab.title")} →
        </a>
      {/if}
    </div>
    {#if canManage}
      <ActionsMenu
        items={[
          editing
            ? { label: t("common.done"), icon: Check, onclick: () => (editing = false) }
            : { label: t("common.edit"), icon: Pencil, onclick: () => (editing = true) },
        ]}
      />
    {/if}
  </div>

  {#if m.needs_connection}
    <!-- No Google account connected anywhere in the org — teach how to connect. -->
    <div class="rounded-lg border border-dashed border-border p-4 text-sm text-text-muted">
      {#if canManage}
        <p>{t("marketing.empty.needs_connection")}</p>
        <a
          href={connectHref(["ga4", "gsc", "gads"])}
          class="mt-2 inline-block font-medium text-brand hover:underline"
        >
          {t("marketing.connect_cta")}
        </a>
      {:else}
        <p>{t("marketing.empty.ask_admin")}</p>
      {/if}
    </div>
  {:else if editing}
    <!-- Edit mode: current links as removable chips + the account pickers. -->
    <div class="space-y-4">
      {#if sources.length > 0}
        <ul class="flex flex-wrap gap-2">
          {#each sources as src (src.link_id)}
            <li
              class="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-sm"
            >
              <span class="text-text-muted">{sourceLabel(src.source)}:</span>
              <span class="text-text">{src.display_name}</span>
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
      <div class="grid gap-4 sm:grid-cols-3">
        {#each SOURCE_ORDER as s (s)}
          <MarketingAccountPicker source={s} linkedIds={linkedIdsBySource[s]} />
        {/each}
      </div>
    </div>
  {:else if sources.length === 0}
    <div class="rounded-lg border border-dashed border-border p-4 text-sm text-text-muted">
      <p>{t("marketing.empty.no_links")}</p>
      {#if canManage}
        <button
          type="button"
          class="mt-2 font-medium text-brand hover:underline"
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
              <span class="text-sm font-semibold text-text">{sourceLabel(src.source)}</span>
              <span class="truncate text-xs text-text-muted">{src.display_name}</span>
              <span
                class="rounded-full px-2 py-0.5 text-[10px] font-medium {healthClass(src.health)}"
              >
                {t(`marketing.health.${src.health}`)}
              </span>
            </div>
            {#if src.deep_link}
              <a
                href={src.deep_link}
                target="_blank"
                rel="noopener noreferrer"
                class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
              >
                {t("marketing.open_in", { source: sourceLabel(src.source) })}
                <ExternalLink size={12} />
              </a>
            {/if}
          </div>

          {#if src.health === "pending"}
            <p class="text-sm text-text-muted">{t("marketing.pending_hint")}</p>
          {:else if src.health === "disconnected"}
            <p class="text-sm text-red-600 dark:text-red-400">{t("marketing.disconnected")}</p>
          {:else}
            <div class="flex flex-wrap items-end gap-4">
              <div class="grid flex-1 grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                {#each headline(src.source) as key (key)}
                  {@const kpi = src.kpis?.[key]}
                  {#if kpi}
                    {@const delta = deltaView(kpi.delta_pct, kpi.lower_is_better)}
                    <a href={tabHref} class="group block">
                      <p class="text-xs text-text-muted">{metricLabel(key)}</p>
                      <p class="text-lg font-semibold tabular-nums text-text group-hover:text-brand">
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
    </div>
  {/if}
{/if}
