<script lang="ts">
  /**
   * Per-source layout editor for the client's marketing tab (issue #192).
   *
   * Edit mode for one source: reorder tiles (↑/↓ — keyboard-friendly, no drag dependency),
   * hide/show them, rename them per locale, choose the enabled drill-downs and the default
   * charted metric. One save per editing surface (docs/UX.md): the whole source posts as one
   * serialized layout through the page's `?/saveLayout` action.
   */
  import { ArrowDown, ArrowUp, X } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  import { drilldownLabel, metricLabel, sourceLabel } from "./format";
  import {
    ALL_METRICS,
    DRILLDOWNS,
    type CompanyLayout,
    type MarketingSource,
    type SourceLayout,
  } from "./types";

  let {
    companyId,
    linkId,
    source,
    layout,
    ondone,
  }: {
    companyId: string;
    /** The source's link id — used to fetch its live key-event names for the labels editor. */
    linkId: string;
    source: MarketingSource;
    /** The company's full stored layout — this editor replaces only its own source entry. */
    layout: CompanyLayout | null | undefined;
    ondone?: () => void;
  } = $props();

  // Deliberate mount-time snapshot: the editor is remounted per source (keyed by the page's
  // editingSource), and edits must not be yanked around by a background payload refresh.
  // svelte-ignore state_referenced_locally
  const all = ALL_METRICS[source] ?? [];
  // svelte-ignore state_referenced_locally
  const allDrilldowns = DRILLDOWNS[source] ?? [];
  // svelte-ignore state_referenced_locally
  const stored: SourceLayout = layout?.sources?.[source] ?? {};

  interface TileRow {
    key: string;
    visible: boolean;
    nl: string;
    en: string;
  }

  // Curated order first (visible tiles), then the hidden rest in default order.
  const initialOrder = [
    ...(stored.tiles ?? all),
    ...all.filter((m) => !(stored.tiles ?? all).includes(m)),
  ].filter((m, i, arr) => arr.indexOf(m) === i);

  let rows = $state<TileRow[]>(
    initialOrder.map((key) => ({
      key,
      visible: (stored.tiles ?? all).includes(key),
      nl: stored.labels?.[key]?.nl ?? "",
      en: stored.labels?.[key]?.en ?? "",
    })),
  );
  let drilldowns = $state<string[]>([...(stored.drilldowns ?? allDrilldowns)]);
  let chartMetric = $state(stored.chart_metric ?? "");
  // "Show this source on the dashboard": the inverse of the layout's `hidden` flag (default on).
  let shown = $state(!(stored.hidden ?? false));

  // Per-key-event labels (GA4 only): each row keyed by the raw GA4 eventName. Seeded from stored
  // labels, then topped up on mount with the live top events so the agency can name what a client
  // actually triggers (contactformulier, aankoop, …) instead of the raw `generate_lead`.
  interface EventRow {
    key: string;
    nl: string;
    en: string;
  }
  // svelte-ignore state_referenced_locally
  let eventRows = $state<EventRow[]>(
    Object.entries(stored.event_labels ?? {}).map(([key, l]) => ({
      key,
      nl: l.nl ?? "",
      en: l.en ?? "",
    })),
  );
  // svelte-ignore state_referenced_locally
  let eventsLoading = $state(source === "ga4");

  onMount(() => {
    if (source !== "ga4") return;
    (async () => {
      try {
        const params = new URLSearchParams({
          company_id: companyId,
          link_id: linkId,
          kind: "key_events",
          range_days: "30",
        });
        const res = await fetch(`/marketing/drilldown?${params}`);
        const data = await res.json();
        const names: string[] = (data?.rows ?? [])
          .map((r: { key?: string | null; label?: string }) => r.key ?? r.label)
          .filter((n: unknown): n is string => typeof n === "string" && n.length > 0);
        const have = new Set(eventRows.map((r) => r.key));
        const add = names
          .filter((n) => !have.has(n))
          .map((key) => ({ key, nl: "", en: "" }));
        if (add.length) eventRows = [...eventRows, ...add];
      } catch {
        // A failed/empty fetch still leaves the stored rows editable — never blocks the editor.
      } finally {
        eventsLoading = false;
      }
    })();
  });

  function removeEvent(key: string) {
    eventRows = eventRows.filter((r) => r.key !== key);
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    [next[index], next[target]] = [next[target], next[index]];
    rows = next;
  }

  function toggleDrilldown(kind: string) {
    drilldowns = drilldowns.includes(kind)
      ? drilldowns.filter((k) => k !== kind)
      : [...drilldowns, kind];
  }

  // The serialized layout: the company's other sources untouched, this one replaced.
  const serialized = $derived.by(() => {
    const visible = rows.filter((r) => r.visible);
    const labels: Record<string, Record<string, string>> = {};
    for (const row of rows) {
      const entry: Record<string, string> = {};
      if (row.nl.trim()) entry.nl = row.nl.trim();
      if (row.en.trim()) entry.en = row.en.trim();
      if (Object.keys(entry).length) labels[row.key] = entry;
    }
    const src: SourceLayout = {
      tiles: visible.map((r) => r.key),
      labels,
      drilldowns: allDrilldowns.filter((k) => drilldowns.includes(k)),
      chart_metric:
        chartMetric && visible.some((r) => r.key === chartMetric) ? chartMetric : null,
    };
    if (!shown) src.hidden = true;
    if (source === "ga4") {
      const eventLabels: Record<string, Record<string, string>> = {};
      for (const row of eventRows) {
        const entry: Record<string, string> = {};
        if (row.nl.trim()) entry.nl = row.nl.trim();
        if (row.en.trim()) entry.en = row.en.trim();
        if (Object.keys(entry).length) eventLabels[row.key] = entry;
      }
      if (Object.keys(eventLabels).length) src.event_labels = eventLabels;
    }
    return JSON.stringify({
      sources: { ...(layout?.sources ?? {}), [source]: src },
    });
  });
</script>

<section class="rounded-xl border border-brand/40 bg-surface-raised p-5">
  <h2 class="mb-3 text-base font-semibold text-text">
    {t("marketing.layout.editing", { source: sourceLabel(source) })}
  </h2>

  <form
    method="POST"
    action="?/saveLayout"
    use:enhance={() =>
      async ({ update }) => {
        await update();
        ondone?.();
      }}
    class="space-y-4"
  >
    <input type="hidden" name="company_id" value={companyId} />
    <input type="hidden" name="layout" value={serialized} />

    <label class="flex items-center gap-2 text-sm text-text">
      <input type="checkbox" bind:checked={shown} />
      {t("marketing.layout.show_source")}
    </label>

    <fieldset>
      <legend class="mb-2 text-sm font-medium text-text">{t("marketing.layout.tiles")}</legend>
      <ul class="space-y-1.5">
        {#each rows as row, i (row.key)}
          <li class="flex flex-wrap items-center gap-2 rounded-lg border border-border px-2 py-1.5">
            <input
              type="checkbox"
              bind:checked={row.visible}
              aria-label={t("marketing.layout.visible", { tile: metricLabel(row.key) })}
            />
            <span class="min-w-28 text-sm {row.visible ? 'text-text' : 'text-text-muted line-through'}">
              {metricLabel(row.key)}
            </span>
            <span class="flex items-center gap-0.5">
              <button
                type="button"
                class="rounded p-1 text-text-muted hover:text-brand disabled:opacity-30"
                disabled={i === 0}
                aria-label={t("marketing.layout.move_up")}
                onclick={() => move(i, -1)}
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                class="rounded p-1 text-text-muted hover:text-brand disabled:opacity-30"
                disabled={i === rows.length - 1}
                aria-label={t("marketing.layout.move_down")}
                onclick={() => move(i, 1)}
              >
                <ArrowDown size={14} />
              </button>
            </span>
            <span class="ml-auto flex min-w-0 flex-wrap items-center gap-1">
              <input
                bind:value={row.nl}
                placeholder="{t('marketing.layout.label_nl')}: {metricLabel(row.key)}"
                maxlength="80"
                class="w-44 min-w-0 rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-brand"
              />
              <input
                bind:value={row.en}
                placeholder={t("marketing.layout.label_en")}
                maxlength="80"
                class="w-36 min-w-0 rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-brand"
              />
            </span>
          </li>
        {/each}
      </ul>
    </fieldset>

    <div class="grid gap-4 sm:grid-cols-2">
      <fieldset>
        <legend class="mb-2 text-sm font-medium text-text">
          {t("marketing.layout.drilldowns")}
        </legend>
        <div class="space-y-1">
          {#each allDrilldowns as kind (kind)}
            <label class="flex items-center gap-2 text-sm text-text">
              <input
                type="checkbox"
                checked={drilldowns.includes(kind)}
                onchange={() => toggleDrilldown(kind)}
              />
              {drilldownLabel(kind)}
            </label>
          {/each}
        </div>
      </fieldset>
      <div>
        <label for="chart-{source}" class="mb-2 block text-sm font-medium text-text">
          {t("marketing.layout.chart_metric")}
        </label>
        <select
          id="chart-{source}"
          bind:value={chartMetric}
          class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand"
        >
          <option value="">{t("marketing.layout.chart_default")}</option>
          {#each rows.filter((r) => r.visible) as row (row.key)}
            <option value={row.key}>{metricLabel(row.key)}</option>
          {/each}
        </select>
      </div>
    </div>

    {#if source === "ga4"}
      <fieldset>
        <legend class="mb-1 text-sm font-medium text-text">
          {t("marketing.layout.key_events")}
        </legend>
        <p class="mb-2 text-xs text-text-muted">{t("marketing.layout.key_events_hint")}</p>
        {#if eventsLoading && eventRows.length === 0}
          <p class="text-sm text-text-muted">{t("marketing.loading")}</p>
        {:else if eventRows.length === 0}
          <p class="text-sm text-text-muted">{t("marketing.layout.no_key_events")}</p>
        {:else}
          <ul class="space-y-1.5">
            {#each eventRows as row (row.key)}
              <li class="flex flex-wrap items-center gap-2 rounded-lg border border-border px-2 py-1.5">
                <code class="min-w-28 break-all text-xs text-text-muted">{row.key}</code>
                <span class="ml-auto flex min-w-0 flex-wrap items-center gap-1">
                  <input
                    bind:value={row.nl}
                    placeholder="{t('marketing.layout.label_nl')}: {row.key}"
                    maxlength="80"
                    class="w-44 min-w-0 rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-brand"
                  />
                  <input
                    bind:value={row.en}
                    placeholder={t("marketing.layout.label_en")}
                    maxlength="80"
                    class="w-36 min-w-0 rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-brand"
                  />
                  <button
                    type="button"
                    class="rounded p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                    aria-label={t("marketing.layout.remove_event", { event: row.key })}
                    onclick={() => removeEvent(row.key)}
                  >
                    <X size={14} />
                  </button>
                </span>
              </li>
            {/each}
          </ul>
        {/if}
      </fieldset>
    {/if}

    <div class="flex gap-2">
      <button class="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
      <button
        type="button"
        class="rounded-lg border border-border px-3 py-2 text-sm text-text"
        onclick={() => ondone?.()}
      >
        {t("common.cancel")}
      </button>
    </div>
  </form>
</section>
