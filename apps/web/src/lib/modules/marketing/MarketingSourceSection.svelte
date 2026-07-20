<script lang="ts">
  /**
   * One source's section on the marketing dashboard (issue #133): KPI tiles for every metric, a
   * trend chart with a metric switcher, the GA4 acquisition split, and the live drill-downs.
   * Renders the KPIs/trend from stored data (instant); only the drill-downs touch Google, lazily.
   *
   * With `edit` set (the dashboard's edit mode, #192 dashboard-style rework) the section becomes
   * its own editor in place, like the My Day board: tiles get a drag handle, an ✕ to hide and
   * inline name fields; hidden tiles wait in a strip below; drill-downs toggle on the card
   * itself; the chart's default metric is a select. Every change calls `onchange` so the host
   * persists immediately.
   */
  import { Eye, EyeOff, ExternalLink, GripVertical, Plus, X } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import TrendChart from "$lib/core/ui/charts/TrendChart.svelte";

  import MarketingDrilldown from "./MarketingDrilldown.svelte";
  import { deltaClass, deltaView, drilldownLabel, fmtMetric, metricLabel, sourceLabel, tileLabel } from "./format";
  import { ALL_METRICS, DRILLDOWNS, type SourceEditState, type SourceMetrics } from "./types";

  let {
    companyId,
    src,
    rangeDays,
    edit = null,
    onchange,
  }: {
    companyId: string;
    src: SourceMetrics;
    rangeDays: number;
    /** The source's edit state while the dashboard's edit mode is on; null = plain view. */
    edit?: SourceEditState | null;
    /** Called after every edit-state mutation — the host serializes and persists. */
    onchange?: () => void;
  } = $props();

  // The charted metric: the source's primary until the user picks another. A plain override
  // (not a reset-on-src effect) keeps the choice when only the *range* changes — the section is
  // keyed by link_id, so `src` is always the same source here.
  let override = $state<string | null>(null);
  const selected = $derived(override ?? src.primary_metric);

  // The API resolves the client's curated layout server-side (#192): `tiles` carries the
  // visible metrics in curated order, `drilldowns` the enabled kinds. In edit mode the local
  // edit state wins, so a drag or hide shows before its round-trip lands.
  const allMetricKeys = $derived(ALL_METRICS[src.source] ?? []);
  const metrics = $derived(
    edit
      ? edit.tiles.map((t) => t.id)
      : src.tiles?.length
        ? src.tiles
        : allMetricKeys,
  );
  const drilldowns = $derived(
    src.drilldowns ??
      (DRILLDOWNS[src.source] ?? []).filter(
        (kind) => kind !== "key_events" || "keyEvents" in (src.kpis ?? {}),
      ),
  );
  const label = (key: string) => tileLabel(key, src.tile_labels);
  const values = $derived(src.series?.metrics?.[selected] ?? []);
  const dates = $derived(src.series?.dates ?? []);

  const channelEntries = $derived(
    src.channels ? Object.entries(src.channels).sort((a, b) => b[1] - a[1]) : [],
  );
  const channelMax = $derived(Math.max(...channelEntries.map(([, v]) => v), 1));

  // ---- Edit mode ---------------------------------------------------------------------------
  const hiddenTiles = $derived(
    edit ? allMetricKeys.filter((key) => !edit!.tiles.some((t) => t.id === key)) : [],
  );
  const allDrilldownKinds = $derived(DRILLDOWNS[src.source] ?? []);
  // The API drops the key-events drill-down while the keyEvents tile is hidden (#134) — mirror
  // that here so the toggle doesn't promise what the server won't deliver.
  const keyEventsLocked = $derived(
    src.source === "ga4" && edit !== null && !edit.tiles.some((t) => t.id === "keyEvents"),
  );

  // Drag only from the grip (the tiles hold text inputs): the recipe from svelte-dnd-action —
  // the zone stays disabled until a handle takes the pointer down, and re-disables on drop.
  let dragEnabled = $state(false);
  function considerTiles(e: CustomEvent<{ items: { id: string }[] }>) {
    if (edit) edit.tiles = e.detail.items;
  }
  function finalizeTiles(e: CustomEvent<{ items: { id: string }[] }>) {
    if (!edit) return;
    // Drop anything that isn't a real metric key (the dnd shadow placeholder) before persisting.
    edit.tiles = e.detail.items.filter((item) => allMetricKeys.includes(item.id));
    dragEnabled = false;
    onchange?.();
  }
  function hideTile(key: string) {
    if (!edit) return;
    edit.tiles = edit.tiles.filter((t) => t.id !== key);
    onchange?.();
  }
  function showTile(key: string) {
    if (!edit) return;
    edit.tiles = [...edit.tiles, { id: key }];
    onchange?.();
  }
  function toggleDrilldown(kind: string) {
    if (!edit) return;
    edit.drilldowns = edit.drilldowns.includes(kind)
      ? edit.drilldowns.filter((k) => k !== kind)
      : [...edit.drilldowns, kind];
    onchange?.();
  }
  function toggleSourceHidden() {
    if (!edit) return;
    edit.hidden = !edit.hidden;
    onchange?.();
  }
</script>

<section
  class="rounded-xl border bg-surface-raised p-5 {edit
    ? 'border-brand/40'
    : 'border-border'} {edit?.hidden ? 'opacity-60' : ''}"
>
  <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
    <div class="flex items-center gap-2">
      <h2 class="text-base font-semibold text-text">{sourceLabel(src.source)}</h2>
      <span class="truncate text-sm text-text-muted">{src.display_name}</span>
      {#if edit?.hidden}
        <span
          class="rounded-lg bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        >
          {t("marketing.layout.source_hidden")}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      {#if edit}
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1 text-xs text-text hover:border-brand"
          onclick={toggleSourceHidden}
        >
          {#if edit.hidden}
            <Eye size={13} /> {t("marketing.layout.show_source")}
          {:else}
            <EyeOff size={13} /> {t("marketing.layout.hide_source")}
          {/if}
        </button>
      {:else if src.deep_link}
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
  </div>

  {#if src.health === "pending"}
    <p class="text-sm text-text-muted">{t("marketing.pending_hint")}</p>
  {:else if src.health === "disconnected"}
    <p class="text-sm text-red-600 dark:text-red-400">{t("marketing.disconnected")}</p>
  {:else if edit}
    <!-- Edit mode: the same tiles, now a drag zone. Tiles without data still render (you can
         arrange a client's dashboard before their first sync). -->
    <div
      class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
      use:dndzone={{
        items: edit.tiles,
        flipDurationMs: 150,
        dropTargetStyle: {},
        type: `marketing-tiles-${src.link_id}`,
        dragDisabled: !dragEnabled,
      }}
      onconsider={considerTiles}
      onfinalize={finalizeTiles}
    >
      {#each edit.tiles as tile (tile.id)}
        {@const kpi = src.kpis?.[tile.id]}
        <div class="relative rounded-lg border border-border bg-surface p-3">
          <button
            type="button"
            onclick={() => hideTile(tile.id)}
            class="absolute -right-2 -top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-raised text-text-muted shadow hover:border-red-400 hover:text-red-500"
            aria-label={t("marketing.layout.hide_tile", { tile: metricLabel(tile.id) })}
          >
            <X size={13} />
          </button>
          <div class="flex items-start gap-1.5">
            <button
              type="button"
              class="-ml-1 mt-0.5 shrink-0 cursor-grab touch-none text-text-muted active:cursor-grabbing"
              aria-label={t("marketing.layout.drag_tile", { tile: metricLabel(tile.id) })}
              onpointerdown={() => (dragEnabled = true)}
            >
              <GripVertical size={14} />
            </button>
            <div class="min-w-0 flex-1">
              <p class="truncate text-xs text-text-muted">{metricLabel(tile.id)}</p>
              <p class="mt-0.5 text-lg font-semibold tabular-nums text-text">
                {kpi ? fmtMetric(tile.id, kpi.current, src.currency) : "—"}
              </p>
            </div>
          </div>
          <!-- Guarded: while dragging, the dnd zone inserts a shadow placeholder item whose id
               is not a metric key — dereferencing labels for it would crash the flip. -->
          {#if edit.labels[tile.id]}
            <div class="mt-2 space-y-1">
              <input
                bind:value={edit.labels[tile.id].nl}
                onchange={() => onchange?.()}
                placeholder="{t('marketing.layout.label_nl')}: {metricLabel(tile.id)}"
                maxlength="80"
                class="w-full rounded border border-border bg-surface-raised px-1.5 py-0.5 text-xs text-text outline-none focus:border-brand"
              />
              <input
                bind:value={edit.labels[tile.id].en}
                onchange={() => onchange?.()}
                placeholder={t("marketing.layout.label_en")}
                maxlength="80"
                class="w-full rounded border border-border bg-surface-raised px-1.5 py-0.5 text-xs text-text outline-none focus:border-brand"
              />
            </div>
          {/if}
        </div>
      {/each}
    </div>

    {#if hiddenTiles.length > 0}
      <div class="mb-5 flex flex-wrap items-center gap-1.5">
        <span class="text-xs text-text-muted">{t("marketing.layout.hidden_tiles")}:</span>
        {#each hiddenTiles as key (key)}
          <button
            type="button"
            onclick={() => showTile(key)}
            class="flex items-center gap-1 rounded-lg border border-dashed border-border px-2 py-1 text-xs text-text-muted hover:border-brand hover:text-brand"
          >
            <Plus size={12} />
            {metricLabel(key)}
          </button>
        {/each}
      </div>
    {/if}

    <!-- Default charted metric, in place of the use-mode chart's tile switcher. -->
    <div class="mb-5 max-w-xs">
      <label for="chart-{src.link_id}" class="mb-1 block text-sm font-medium text-text">
        {t("marketing.layout.chart_metric")}
      </label>
      <select
        id="chart-{src.link_id}"
        bind:value={edit.chart_metric}
        onchange={() => onchange?.()}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand"
      >
        <option value="">{t("marketing.layout.chart_default")}</option>
        {#each edit.tiles as tile (tile.id)}
          <option value={tile.id}>{metricLabel(tile.id)}</option>
        {/each}
      </select>
    </div>

    <!-- Drill-downs: enabled ones live with an ✕, disabled ones as quiet placeholders — no
         fetch until they're actually shown (docs/PERFORMANCE.md). -->
    <div class="grid gap-5 md:grid-cols-2">
      {#each allDrilldownKinds as kind (kind)}
        {@const enabled = edit.drilldowns.includes(kind) && !(kind === "key_events" && keyEventsLocked)}
        {#if enabled}
          <div class="relative rounded-lg border border-border p-3">
            <button
              type="button"
              onclick={() => toggleDrilldown(kind)}
              class="absolute -right-2 -top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-raised text-text-muted shadow hover:border-red-400 hover:text-red-500"
              aria-label={t("marketing.layout.hide_drilldown", { name: drilldownLabel(kind) })}
            >
              <X size={13} />
            </button>
            {#key rangeDays}
              <MarketingDrilldown
                {companyId}
                linkId={src.link_id}
                source={src.source}
                {kind}
                {rangeDays}
                currency={src.currency}
                {edit}
                {onchange}
              />
            {/key}
          </div>
        {:else}
          <div
            class="flex min-h-24 flex-col items-start justify-center gap-2 rounded-lg border border-dashed border-border p-3"
          >
            <p class="text-sm font-medium text-text-muted">{drilldownLabel(kind)}</p>
            {#if kind === "key_events" && keyEventsLocked}
              <p class="text-xs text-text-muted">{t("marketing.layout.key_events_locked")}</p>
            {:else}
              <button
                type="button"
                onclick={() => toggleDrilldown(kind)}
                class="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-text hover:border-brand hover:text-brand"
              >
                <Plus size={12} />
                {t("marketing.layout.show_drilldown")}
              </button>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {:else}
    <!-- KPI tiles: every metric the source carries, with its period-over-period delta. -->
    <div class="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {#each metrics as key (key)}
        {@const kpi = src.kpis?.[key]}
        {#if kpi}
          {@const delta = deltaView(kpi.delta_pct, kpi.lower_is_better)}
          <button
            type="button"
            onclick={() => (override = key)}
            class="rounded-lg border p-3 text-left transition-colors {selected === key
              ? 'border-brand bg-surface'
              : 'border-border hover:border-brand/50'}"
          >
            <p class="text-xs text-text-muted">{label(key)}</p>
            <p class="mt-0.5 text-lg font-semibold tabular-nums text-text">
              {fmtMetric(key, kpi.current, src.currency)}
            </p>
            {#if delta}
              <p class="text-xs tabular-nums {deltaClass(delta.tone)}">
                {delta.text} <span class="text-text-muted">{t("marketing.vs_previous")}</span>
              </p>
            {/if}
          </button>
        {/if}
      {/each}
    </div>

    <!-- Trend of the selected metric. -->
    <div class="mb-5">
      <p class="mb-1 text-sm font-medium text-text">{label(selected)}</p>
      <TrendChart
        {dates}
        {values}
        label={label(selected)}
        format={(v) => fmtMetric(selected, v, src.currency)}
      />
    </div>

    {#if channelEntries.length}
      <div class="mb-5">
        <p class="mb-2 text-sm font-medium text-text">{t("marketing.channels_title")}</p>
        <ul class="space-y-1.5">
          {#each channelEntries as [name, value] (name)}
            <li class="flex items-center gap-2 text-sm">
              <span class="w-32 shrink-0 truncate text-text-muted">{name}</span>
              <span class="h-2 flex-1 overflow-hidden rounded-full bg-surface">
                <span
                  class="block h-full rounded-full bg-brand"
                  style="width: {(value / channelMax) * 100}%"
                ></span>
              </span>
              <span class="w-16 shrink-0 text-right tabular-nums text-text">{fmtNumber(value, 0)}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <!-- Live drill-downs (only these touch Google), keyed so a range change re-fetches. -->
    <div class="grid gap-5 md:grid-cols-2">
      {#each drilldowns as kind (kind)}
        {#key rangeDays}
          <MarketingDrilldown
            {companyId}
            linkId={src.link_id}
            source={src.source}
            {kind}
            {rangeDays}
            currency={src.currency}
          />
        {/key}
      {/each}
    </div>
  {/if}
</section>
