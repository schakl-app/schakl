<script lang="ts">
  /**
   * How visible a client's brand is inside AI answers — last month against the month before
   * (docs/SERANKING.md). SE Ranking's AI Search overview: four figures, a monthly trend, and a
   * plain-language account of what each figure is, because "brand presence 50" is a number a
   * client cannot name and an agency should not have to explain from memory.
   *
   * Three things this block is careful about.
   *
   * **The months are named, never implied.** "vorige maand" is a sentence that can be printed
   * over any two months; "augustus 2026 · vergeleken met juli 2026" can be checked (#312). And
   * where SE Ranking has not published the month that was asked about, the figures keep the
   * name of the month they really are.
   *
   * **A refusal decides a sentence, never whether the block is drawn** (#399): a key without
   * Data API access, a plan out of units and an outage are three sentences with three different
   * people who can fix them — shown to whoever can, and to nobody else. A client reads figures
   * or nothing (§15, #274).
   *
   * **The settings live where the numbers are.** Target and brand are facts about *this*
   * client, so they are edited here (the dashboard's own rule for the comparison), over house
   * defaults from Instellingen → Marketing; every blank says what it inherits. The two controls
   * that spend the agency's units say how many, on the control (#305).
   */
  import Info from "@lucide/svelte/icons/info";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Settings2 from "@lucide/svelte/icons/settings-2";
  import Sparkles from "@lucide/svelte/icons/sparkles";

  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import { fmtMonthYear, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import TrendChart from "$lib/core/ui/charts/TrendChart.svelte";

  import { deltaClass } from "../format";
  import {
    AI_ENGINES,
    AI_SCOPES,
    AI_SERIES,
    UNITS_BRAND_LOOKUP,
    UNITS_PER_ENGINE,
    type AiEngine,
    type AiSearchEngineBlock,
    type AiSearchMetric,
    type AiSearchOverview,
    type AiSeries,
  } from "./types";

  type Streamed = { data: AiSearchOverview | null; errorKey: string | null } | null;

  let {
    companyId,
    overview: incoming,
    isPortal = false,
  }: {
    companyId: string;
    /** The streamed read (docs/PERFORMANCE.md): a first view may be SE Ranking's latency, and
     *  the dashboard's shell must not wait for it. */
    overview: Promise<Streamed>;
    isPortal?: boolean;
  } = $props();

  // Resolved into `$state` rather than awaited in the markup: a raw `{#await}` drops to its
  // pending branch on every invalidation, which would close the settings editor under the
  // person typing in it (the dashboard's own reason for doing the same with its metrics).
  //
  // `$state.raw`, and the effect reads nothing it writes: `overview` is a deep proxy otherwise,
  // so assigning the same payload again makes a *new* proxy — a change — and an effect that
  // read it would re-run, re-assign and spin the main thread for ever. Found by the first
  // browser pass, which simply never came back.
  let overview = $state.raw<AiSearchOverview | null>(null);
  let pending = $state(true);
  let loadError = $state<string | null>(null);
  let loaded = false; // plain on purpose: the effect must not depend on it
  $effect(() => {
    const promise = incoming;
    // "Loading" only before the first answer. A later invalidation keeps the figures on
    // screen while it re-reads, so an open editor is never pulled out from under its user.
    if (!loaded) pending = true;
    void promise.then((value) => {
      if (incoming !== promise) return;
      overview = value?.data ?? null;
      loadError = value?.errorKey ?? null;
      loaded = true;
      pending = false;
    });
  });

  const busy = new InFlight();
  const canManage = $derived(Boolean(overview?.can_manage) && !isPortal);
  const blocks = $derived(overview?.engines ?? []);

  // ---- which engine, which stream ------------------------------------------------------------
  let pickedEngine = $state<AiEngine | null>(null);
  const block = $derived<AiSearchEngineBlock | null>(
    blocks.find((b) => b.engine === pickedEngine) ?? blocks[0] ?? null,
  );
  let pickedSeries = $state<AiSeries>("link_presence");
  const streams = $derived(AI_SERIES.filter((key) => (block?.series?.[key]?.length ?? 0) > 1));
  const stream = $derived<AiSeries | null>(
    streams.includes(pickedSeries) ? pickedSeries : (streams[0] ?? null),
  );
  const points = $derived(stream ? (block?.series?.[stream] ?? []) : []);
  const metrics = $derived(block?.metrics ?? []);

  const month = (iso: string | null | undefined) => (iso ? fmtMonthYear(iso.slice(0, 7)) : "");
  const dataMonth = $derived(month(block?.data_month));
  const compareMonth = $derived(month(block?.compare_month));
  const askedMonth = $derived(month(block?.period_month ?? overview?.period_month));
  /** SE Ranking answered for an older month than the one asked about. */
  const lagging = $derived(
    Boolean(block?.data_month && block?.period_month && block.data_month < block.period_month),
  );

  function value(metric: AiSearchMetric["key"] | AiSeries, v: number | null | undefined): string {
    if (v === null || v === undefined) return "–";
    return fmtNumber(v, metric === "average_position" ? 1 : 0);
  }
  function tone(metric: AiSearchMetric): "up" | "down" | "flat" {
    return metric.verdict === "good" ? "up" : metric.verdict === "bad" ? "down" : "flat";
  }
  function moved(metric: AiSearchMetric): string | null {
    if (metric.change_percent === null || metric.change_percent === undefined) {
      if (metric.change_absolute === null || metric.change_absolute === undefined) return null;
      const abs = metric.change_absolute;
      return `${abs > 0 ? "+" : ""}${fmtNumber(abs, 1)}`;
    }
    const arrow = metric.direction === "up" ? "▲" : metric.direction === "down" ? "▼" : "";
    const pct = metric.change_percent;
    return `${arrow} ${pct > 0 ? "+" : ""}${fmtNumber(pct, 1)}%`.trim();
  }

  // ---- a read in flight somewhere else ---------------------------------------------------------
  // Another request holds the claim (a colleague, the report worker). Ask again shortly, a few
  // times only — a block that polls for ever is a block that hides a stuck claim.
  let polls = 0;
  $effect(() => {
    if (!blocks.some((b) => b.status === "fetching") || polls >= 4) return;
    const timer = setTimeout(() => {
      polls += 1;
      void invalidateAll();
    }, 5000);
    return () => clearTimeout(timer);
  });

  // ---- the editor ------------------------------------------------------------------------------
  let editing = $state(false);
  let showHelp = $state(false);
  /** A re-read SE Ranking refused: the stored figures stayed, and this is the reason. */
  let refreshNotice = $state<string | null>(null);
  let formError = $state<string | null>(null);
  let brandInput = $state("");
  let brandOptions = $state<string[] | null>(null);
  let ownEngines = $state(false);
  let engineTicks = $state<AiEngine[]>([]);

  const own = $derived((overview?.own ?? null) as Record<string, unknown> | null);
  const house = $derived(overview?.house ?? null);
  function openEditor() {
    brandInput = String(own?.brand ?? "");
    const stored = (own?.engines as AiEngine[] | undefined) ?? [];
    ownEngines = stored.length > 0;
    engineTicks = stored.length ? [...stored] : [...(house?.engines ?? ["all"])];
    brandOptions = null;
    formError = null;
    editing = true;
  }
  const enabledValue = $derived(own?.enabled === true ? "on" : own?.enabled === false ? "off" : "");
  const editorUnits = $derived(
    (ownEngines ? Math.max(engineTicks.length, 1) : (house?.engines.length ?? 1)) *
      UNITS_PER_ENGINE,
  );
  const refreshUnits = $derived((overview?.settings.engines.length ?? 1) * UNITS_PER_ENGINE);
  function toggleEngine(engine: AiEngine) {
    engineTicks = engineTicks.includes(engine)
      ? engineTicks.filter((e) => e !== engine)
      : [...engineTicks, engine];
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const statusKey = (status: string) =>
    ["denied", "insufficient", "failed", "fetching", "missing"].includes(status)
      ? `marketing.ai_search.status.${status}`
      : null;
</script>

{#if pending}
  <section class="rounded-xl border border-dashed border-border bg-surface-raised p-5">
    <p class="text-sm text-text-muted">{t("marketing.ai_search.loading")}</p>
  </section>
{:else if overview && (overview.state !== "off" || canManage)}
  <section
    class="rounded-xl border border-border bg-surface-raised p-5"
    aria-labelledby="ai-search-title"
    data-testid="ai-search"
  >
    <header class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h2 id="ai-search-title" class="flex items-center gap-2 text-base font-semibold text-text">
          <Sparkles size={16} class="text-brand" aria-hidden="true" />
          {t("marketing.ai_search.title")}
        </h2>
        {#if overview.state === "ready" && block?.data_month}
          <!-- Both months, by name: a percentage is a claim about two spans (#312). -->
          <p class="mt-1 text-sm text-text-muted" data-testid="ai-search-period">
            {compareMonth
              ? t("marketing.ai_search.period", { month: dataMonth, compare: compareMonth })
              : dataMonth}
          </p>
        {:else}
          <p class="mt-1 text-sm text-text-muted">{t("marketing.ai_search.subtitle")}</p>
        {/if}
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm text-text hover:border-brand"
          aria-expanded={showHelp}
          onclick={() => (showHelp = !showHelp)}
        >
          <Info size={14} aria-hidden="true" />
          {t("marketing.ai_search.help.toggle")}
        </button>
        {#if canManage}
          <button
            type="button"
            class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm {editing
              ? 'border-brand text-brand'
              : 'border-border text-text hover:border-brand'}"
            aria-expanded={editing}
            onclick={() => (editing ? (editing = false) : openEditor())}
          >
            <Settings2 size={14} aria-hidden="true" />
            {t("marketing.ai_search.settings.open")}
          </button>
        {/if}
      </div>
    </header>

    {#if showHelp}
      <!-- Written out, not hidden behind a hover: half of these are read on a phone, and the
           words are the point — a client asked "wat is merkaanwezigheid?" deserves one answer. -->
      <dl
        class="mb-5 grid gap-x-6 gap-y-3 rounded-lg bg-surface p-4 text-sm sm:grid-cols-2"
        data-testid="ai-search-help"
      >
        {#each ["brand_presence", "link_presence", "average_position", "ai_opportunity_traffic"] as key (key)}
          <div>
            <dt class="font-medium text-text">{t(`marketing.ai_search.metric.${key}`)}</dt>
            <dd class="mt-0.5 text-text-muted">{t(`marketing.ai_search.help.${key}`)}</dd>
          </div>
        {/each}
        <div class="sm:col-span-2">
          <dt class="font-medium text-text">{t("marketing.ai_search.help.source_title")}</dt>
          <!-- A client never reads the agency's supplier's name (#446): same facts, no vendor. -->
          <dd class="mt-0.5 text-text-muted">
            {t(
              isPortal
                ? "marketing.ai_search.help.source_portal"
                : "marketing.ai_search.help.source",
            )}
          </dd>
        </div>
      </dl>
    {/if}

    {#if editing && canManage}
      <form
        method="POST"
        action="?/marketingAiSearchSettings"
        class="mb-5 space-y-4 rounded-lg border border-brand/40 bg-surface p-4"
        data-testid="ai-search-editor"
        use:enhance={busy.wrap(
          // Two submit buttons, one form: the brand lookup rides `formaction`, so the key (and
          // the spinner) follow whichever was pressed.
          (input) => (input.action.search.includes("Brand") ? "brand" : "settings"),
          () =>
            async ({ result, update }) => {
              formError = null;
              const found =
                result.type === "success" ? (result.data?.aiSearchBrands as unknown) : undefined;
              if (Array.isArray(found)) {
                // The lookup answers *into* the box, where somebody who knows the client can
                // see it is right before it is saved. Nothing was written, so nothing reloads.
                brandOptions = found.map(String);
                if (brandOptions.length === 1 && !brandInput.trim()) brandInput = brandOptions[0];
                await update({ reset: false, invalidateAll: false });
                return;
              }
              // An edit form: never reset (docs/UX.md) — the values are this client's settings.
              await update({ reset: false });
              if (result.type === "success") editing = false;
              else if (result.type === "failure")
                formError = String(result.data?.error ?? "errors.server");
            },
        )}
      >
        <input type="hidden" name="company_id" value={companyId} />
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label for="ai-enabled" class="mb-1 block text-sm font-medium text-text">
              {t("marketing.ai_search.settings.enabled")}
            </label>
            <select id="ai-enabled" name="enabled" value={enabledValue} class={inputClass}>
              <option value="">
                {t("marketing.ai_search.settings.inherit", {
                  value: t(
                    house?.enabled
                      ? "marketing.ai_search.settings.on"
                      : "marketing.ai_search.settings.off",
                  ),
                })}
              </option>
              <option value="on">{t("marketing.ai_search.settings.on")}</option>
              <option value="off">{t("marketing.ai_search.settings.off")}</option>
            </select>
          </div>
          <div>
            <label for="ai-target" class="mb-1 block text-sm font-medium text-text">
              {t("marketing.ai_search.settings.target")}
            </label>
            <input
              id="ai-target"
              name="target"
              value={String(own?.target ?? "")}
              placeholder={own?.target ? "" : overview.settings.target}
              maxlength="512"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">
              {t("marketing.ai_search.settings.target_hint")}
            </p>
          </div>
          <div>
            <label for="ai-brand" class="mb-1 block text-sm font-medium text-text">
              {t("marketing.ai_search.settings.brand")}
            </label>
            <input
              id="ai-brand"
              name="brand"
              bind:value={brandInput}
              placeholder={overview.brand_origin === "discovered" ? overview.brand : ""}
              maxlength="255"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">
              {t("marketing.ai_search.settings.brand_hint")}
            </p>
            {#if brandOptions}
              <div class="mt-2 flex flex-wrap items-center gap-1.5" data-testid="ai-brand-options">
                {#if brandOptions.length === 0}
                  <span class="text-xs text-text-muted">
                    {t("marketing.ai_search.settings.brand_none")}
                  </span>
                {/if}
                {#each brandOptions as option (option)}
                  <button
                    type="button"
                    class="rounded-full border border-border px-2.5 py-0.5 text-xs text-text hover:border-brand"
                    onclick={() => (brandInput = option)}
                  >
                    {option}
                  </button>
                {/each}
              </div>
            {/if}
            <!-- `formaction`, so the lookup posts this form's client without a nested form. -->
            <button
              type="submit"
              formaction="?/marketingAiSearchBrand"
              formnovalidate
              class="mt-2 text-xs font-medium text-brand hover:underline disabled:opacity-50"
              disabled={busy.active}
              data-testid="ai-brand-lookup"
            >
              {busy.is("brand")
                ? t("marketing.ai_search.settings.brand_looking")
                : t("marketing.ai_search.settings.brand_lookup", { units: UNITS_BRAND_LOOKUP })}
            </button>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label for="ai-source" class="mb-1 block text-sm font-medium text-text">
                {t("marketing.ai_search.settings.source")}
              </label>
              <input
                id="ai-source"
                name="source"
                value={String(own?.source ?? "")}
                placeholder={house?.source ?? "nl"}
                maxlength="2"
                class="{inputClass} uppercase"
              />
            </div>
            <div>
              <label for="ai-scope" class="mb-1 block text-sm font-medium text-text">
                {t("marketing.ai_search.settings.scope")}
              </label>
              <select
                id="ai-scope"
                name="scope"
                value={String(own?.scope ?? "")}
                class={inputClass}
              >
                <option value="">
                  {t("marketing.ai_search.settings.inherit", {
                    value: t(`marketing.ai_search.scope.${house?.scope ?? "base_domain"}`),
                  })}
                </option>
                {#each AI_SCOPES as scope (scope)}
                  <option value={scope}>{t(`marketing.ai_search.scope.${scope}`)}</option>
                {/each}
              </select>
            </div>
            <p class="col-span-2 text-xs text-text-muted">
              {t("marketing.ai_search.settings.source_hint")}
            </p>
          </div>
        </div>

        <fieldset>
          <legend class="mb-1 text-sm font-medium text-text">
            {t("marketing.ai_search.settings.engines")}
          </legend>
          <label class="mb-2 flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              name="engines_mode"
              value="own"
              bind:checked={ownEngines}
              class="rounded border-border"
            />
            {t("marketing.ai_search.settings.engines_own")}
          </label>
          <div class="flex flex-wrap gap-x-4 gap-y-1.5" class:opacity-50={!ownEngines}>
            {#each AI_ENGINES as engine (engine)}
              <label class="flex items-center gap-1.5 text-sm text-text">
                <input
                  type="checkbox"
                  name="engines"
                  value={engine}
                  checked={engineTicks.includes(engine)}
                  disabled={!ownEngines}
                  onchange={() => toggleEngine(engine)}
                  class="rounded border-border"
                />
                {t(`marketing.ai_search.engine.${engine}`)}
              </label>
            {/each}
          </div>
          <p class="mt-2 text-xs text-text-muted" data-testid="ai-search-cost">
            {t("marketing.ai_search.settings.cost", { units: fmtNumber(editorUnits, 0) })}
          </p>
        </fieldset>

        {#if formError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(formError)}</p>
        {/if}
        <div class="flex items-center gap-2">
          <Button type="submit" loading={busy.is("settings")}>{t("common.save")}</Button>
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm text-text-muted hover:text-text"
            onclick={() => (editing = false)}
          >
            {t("common.cancel")}
          </button>
        </div>
      </form>
    {/if}

    {#if loadError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(loadError)}</p>
    {:else if overview.state === "off"}
      <!-- Only a manager reaches this branch. Off is the default on purpose: every read spends
           the agency's own units, so switching it on is a decision, and this says what it buys. -->
      <div class="rounded-lg border border-dashed border-border p-4" data-testid="ai-search-off">
        <p class="text-sm text-text">{t("marketing.ai_search.off.body")}</p>
        <p class="mt-1 text-xs text-text-muted">
          {t("marketing.ai_search.off.cost", { units: fmtNumber(UNITS_PER_ENGINE, 0) })}
        </p>
        <div class="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            class="text-sm font-medium text-brand hover:underline"
            onclick={openEditor}
          >
            {t("marketing.ai_search.off.enable_client")}
          </button>
          <a href="/settings/marketing#ai-search" class="text-sm text-text-muted hover:text-text">
            {t("marketing.ai_search.off.enable_house")}
          </a>
        </div>
      </div>
    {:else if overview.state === "no_key"}
      <p class="text-sm text-text-muted">
        {t("marketing.ai_search.state.no_key")}
        <a href="/settings/marketing" class="font-medium text-brand hover:underline">
          {t("marketing.ai_search.state.open_settings")}
        </a>
      </p>
    {:else if overview.state === "no_target"}
      <p class="text-sm text-text-muted">{t("marketing.ai_search.state.no_target")}</p>
    {:else if block}
      {#if blocks.length > 1}
        <div class="mb-4 flex flex-wrap gap-1" role="tablist">
          {#each blocks as b (b.engine)}
            <button
              type="button"
              role="tab"
              aria-selected={block.engine === b.engine}
              class="rounded-lg px-3 py-1.5 text-sm font-medium {block.engine === b.engine
                ? 'bg-brand text-white'
                : 'text-text-muted hover:bg-surface'}"
              onclick={() => (pickedEngine = b.engine)}
            >
              {t(`marketing.ai_search.engine.${b.engine}`)}
            </button>
          {/each}
        </div>
      {/if}

      {#if canManage && refreshNotice && statusKey(refreshNotice)}
        <p
          class="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
          data-testid="ai-search-notice"
        >
          {t(statusKey(refreshNotice) ?? "", { month: askedMonth })}
          {t("marketing.ai_search.kept")}
        </p>
      {/if}
      {#if canManage && statusKey(block.status)}
        <!-- To whoever can act on it, and in which of the three ways it went wrong. -->
        <p
          class="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
          data-testid="ai-search-status"
        >
          {t(statusKey(block.status) ?? "", { month: askedMonth })}
        </p>
      {/if}

      {#if metrics.length}
        {#if lagging}
          <p class="mb-3 text-xs text-text-muted" data-testid="ai-search-lagging">
            {t(isPortal ? "marketing.ai_search.lagging_portal" : "marketing.ai_search.lagging", {
              asked: askedMonth,
              month: dataMonth,
            })}
          </p>
        {/if}
        <div class="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="ai-search-tiles">
          {#each metrics as metric (metric.key)}
            {@const change = moved(metric)}
            <div class="flex flex-col rounded-lg bg-surface-tint p-3">
              <p class="text-xs text-text-muted">{t(`marketing.ai_search.metric.${metric.key}`)}</p>
              <p class="mt-0.5 text-lg font-semibold tabular-nums text-text">
                {value(metric.key, metric.current)}
              </p>
              {#if change}
                <p class="text-xs tabular-nums {deltaClass(tone(metric))}">
                  {change}
                  <span class="text-text-muted">
                    {t("marketing.ai_search.was", { value: value(metric.key, metric.previous) })}
                  </span>
                </p>
              {:else if metric.current !== null && metric.current !== undefined}
                <!-- No month before it to compare with. Said, because SE Ranking's own API
                     reports this case as "+100 %", which is a baseline dressed as growth. -->
                <p class="text-xs text-text-muted">{t("marketing.ai_search.no_previous")}</p>
              {/if}
              <p class="mt-auto pt-2 text-[11px] leading-snug text-text-muted">
                {t(`marketing.ai_search.short.${metric.key}`)}
              </p>
            </div>
          {/each}
        </div>

        {#if stream && points.length > 1}
          <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p class="text-sm font-medium text-text">{t(`marketing.ai_search.series.${stream}`)}</p>
            {#if streams.length > 1}
              <div class="flex flex-wrap gap-1">
                {#each streams as key (key)}
                  <button
                    type="button"
                    class="rounded-md px-2 py-1 text-xs {stream === key
                      ? 'bg-surface text-text'
                      : 'text-text-muted hover:text-text'}"
                    aria-pressed={stream === key}
                    onclick={() => (pickedSeries = key)}
                  >
                    {t(`marketing.ai_search.series.${key}`)}
                  </button>
                {/each}
              </div>
            {/if}
          </div>
          <TrendChart
            dates={points.map((p) => p.month)}
            values={points.map((p) => p.value)}
            label={t(`marketing.ai_search.series.${stream}`)}
            format={(v) => value(stream, v)}
            dateLabel={fmtMonthYear}
          />
        {/if}
        {#if block.realigned}
          <p class="mt-2 text-xs text-text-muted">
            {t(isPortal ? "marketing.ai_search.realigned_portal" : "marketing.ai_search.realigned")}
          </p>
        {/if}
      {:else if !statusKey(block.status)}
        <p class="text-sm text-text-muted">{t("marketing.ai_search.empty")}</p>
      {/if}

      <!-- Whose mentions, which site, which country: a figure attributed to the wrong brand
           looks exactly like a figure, so the attribution is printed beside it. -->
      <footer
        class="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border pt-3 text-xs text-text-muted"
      >
        <p data-testid="ai-search-subject">
          {#if overview.brand}
            {t("marketing.ai_search.subject", {
              brand: overview.brand,
              target: overview.settings.target,
              country: overview.settings.source.toUpperCase(),
            })}
          {:else}
            <!-- Who attributed the brand is the supplier's name, which a client never reads (#446). -->
            {t(
              isPortal
                ? "marketing.ai_search.subject_no_brand_portal"
                : "marketing.ai_search.subject_no_brand",
              {
                target: overview.settings.target,
                country: overview.settings.source.toUpperCase(),
              },
            )}
          {/if}
          {#if canManage && overview.brand_origin === "discovered"}
            <span>· {t("marketing.ai_search.brand_discovered")}</span>
          {/if}
        </p>
        {#if canManage}
          <form
            method="POST"
            action="?/marketingAiSearchRefresh"
            use:enhance={busy.wrap("refresh", () => async ({ result, update }) => {
              polls = 0;
              refreshNotice =
                result.type === "success"
                  ? ((result.data?.aiSearchNotice as string | null | undefined) ?? null)
                  : result.type === "failure"
                    ? "failed"
                    : null;
              await update({ reset: false });
            })}
          >
            <input type="hidden" name="company_id" value={companyId} />
            <button
              type="submit"
              class="inline-flex items-center gap-1.5 font-medium text-text hover:text-brand disabled:opacity-50"
              disabled={busy.active}
              data-testid="ai-search-refresh"
            >
              <RefreshCw
                size={12}
                class={busy.is("refresh") ? "animate-spin" : ""}
                aria-hidden="true"
              />
              {t("marketing.ai_search.refresh", { units: fmtNumber(refreshUnits, 0) })}
            </button>
          </form>
        {/if}
      </footer>
      {#if canManage && !overview.brand_fits}
        <p
          class="mt-2 text-xs text-amber-700 dark:text-amber-300"
          data-testid="ai-search-brand-hint"
        >
          {t("marketing.ai_search.brand_mismatch", { brand: overview.brand })}
        </p>
      {/if}
    {/if}
  </section>
{/if}
