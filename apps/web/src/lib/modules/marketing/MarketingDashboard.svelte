<script lang="ts">
  /**
   * The marketing dashboard for one client — range tabs, website filter, per-source sections —
   * shared by the top-level Marketing page and the client's marketing tab, so both surfaces are
   * the same screen with the same edit affordance (owner feedback: they used to differ).
   *
   * Edit mode works like the My Day board (#192 → dashboard-style rework): the pencil turns the
   * real sections editable in place — drag tiles to reorder, ✕ to hide, name them inline, toggle
   * drill-downs, relabel key events in the table itself — and every change persists immediately
   * through the host page's `?/saveLayout` action. No separate editor form.
   */
  import { Check, Pencil } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { editLocales } from "$lib/core/i18n-edit.svelte";
  import I18nLocaleSwitcher from "$lib/core/ui/I18nLocaleSwitcher.svelte";

  import { comparePeriodLabel, compareModeLabel, currentPeriodLabel } from "./format";
  import MarketingPeriodPicker from "./MarketingPeriodPicker.svelte";
  import MarketingSourceSection from "./MarketingSourceSection.svelte";
  import { anchorMonth, PERIOD_PRESETS } from "./periods";
  import {
    ALL_METRICS,
    COMPARE_PERIODS,
    DRILLDOWNS,
    connectHref,
    type CompanyMarketing,
    type MarketingSource,
    type SourceEditState,
    type SourceLayout,
    type SourceMetrics,
  } from "./types";

  let {
    companyId,
    metrics,
    pending = false,
    range,
    website,
    urlFor,
    manageHref,
    onconnect = undefined,
  }: {
    companyId: string;
    metrics: CompanyMarketing | null;
    /** The payload is still in flight (it streams — docs/PERFORMANCE.md). "Nothing linked yet" is
     *  a wrong answer while that is true, not a slow one, so the shell says "loading" instead. */
    pending?: boolean;
    range: string;
    website: string;
    /** Builds the page's own URL for a range/website pick (the two hosts differ in query shape). */
    urlFor: (range: string, website: string) => string;
    /** Where the empty state sends the user to link accounts (the client page). */
    manageHref: string;
    /**
     * Open this host's connect dialog (#399). When a host can offer the gesture *here*, the
     * empty state does, rather than sending the reader to another screen to find the ＋ behind
     * ⋯ → Bewerken. Absent on a host that has no dialog, where `manageHref` remains the answer.
     */
    onconnect?: (() => void) | undefined;
  } = $props();

  const allSources = $derived(metrics?.sources ?? []);
  // Website filter: "" shows everything, "client" narrows to client-level links, else one site.
  const filteredByWebsite = $derived(
    allSources.filter((s) =>
      !website ? true : website === "client" ? !s.website_id : s.website_id === website,
    ),
  );
  // A hidden source (#192) is only in the payload for a manager; it renders dimmed in edit mode
  // (with a re-enable toggle) and stays out of the read view, matching what the client sees.
  const sources = $derived(filteredByWebsite.filter((s) => editMode || !s.hidden));
  const websites = $derived(metrics?.websites ?? []);
  const hasClientLevel = $derived(allSources.some((s) => !s.website_id));

  // Group the sources per client website; links without one form a trailing client-level group.
  type WebsiteGroup = { id: string | null; name: string | null; sources: SourceMetrics[] };
  const groups = $derived.by(() => {
    const out: WebsiteGroup[] = [];
    for (const src of sources) {
      const id = src.website_id ?? null;
      let group = out.find((g) => g.id === id);
      if (!group) {
        group = { id, name: src.website_name ?? null, sources: [] };
        out.push(group);
      }
      group.sources.push(src);
    }
    out.sort((a, b) =>
      a.id === null ? 1 : b.id === null ? -1 : (a.name ?? "").localeCompare(b.name ?? ""),
    );
    return out;
  });
  const showGroupHeadings = $derived(groups.some((g) => g.id !== null));

  const rangeClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;

  // ---- Edit mode (use vs edit, docs/UX.md §3) --------------------------------------------
  let editMode = $state(false);
  // Seeded from the stored layout when the pencil turns on; the sections mutate it in place.
  // Deliberately NOT re-seeded on payload refresh: a save invalidates the page data, and edits
  // in progress must not be yanked around by their own round-trip.
  let edit = $state<Partial<Record<MarketingSource, SourceEditState>>>({});

  /** One editable slot per locale, seeded from what is stored. Any locale the store carries but
   *  the app no longer ships is kept: dropping it here would delete it on the next save. */
  function blankLabels(
    locales: string[],
    stored: Record<string, string> | undefined,
  ): Record<string, string> {
    const out: Record<string, string> = {};
    for (const locale of locales) out[locale] = "";
    for (const [locale, value] of Object.entries(stored ?? {})) out[locale] = value ?? "";
    return out;
  }

  function seedEdit(): Partial<Record<MarketingSource, SourceEditState>> {
    const out: Partial<Record<MarketingSource, SourceEditState>> = {};
    for (const src of allSources) {
      if (out[src.source]) continue; // two links of one source share one layout entry
      const all = ALL_METRICS[src.source] ?? [];
      const stored: SourceLayout = metrics?.layout?.sources?.[src.source] ?? {};
      const visible = (stored.tiles ?? all).filter((k) => all.includes(k));
      // Every locale gets an entry so the inputs can bind straight into it — a label the tenant
      // never typed is an empty string, not a missing key the editor would have to create mid-render.
      const locales = editLocales();
      const labels: Record<string, Record<string, string>> = {};
      for (const key of all) {
        labels[key] = blankLabels(locales, stored.labels?.[key]);
      }
      const event_labels: Record<string, Record<string, string>> = {};
      for (const [key, l] of Object.entries(stored.event_labels ?? {})) {
        event_labels[key] = blankLabels(locales, l);
      }
      out[src.source] = {
        tiles: visible.map((id) => ({ id })),
        labels,
        drilldowns: [...(stored.drilldowns ?? DRILLDOWNS[src.source] ?? [])],
        chart_metric: stored.chart_metric ?? "",
        event_labels,
        hidden: stored.hidden ?? false,
      };
    }
    return out;
  }

  function toggleEdit() {
    if (!editMode) edit = seedEdit();
    editMode = !editMode;
  }

  /** Only the languages actually typed in — a blank one is "no override", never a stored "". */
  function filledLabels(labels: Record<string, string>): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [locale, value] of Object.entries(labels)) {
      if (value?.trim()) out[locale] = value.trim();
    }
    return out;
  }

  // The serialized layout: sources not on this screen carried through untouched, edited ones
  // rebuilt under the same rules the old editor used (empty labels dropped, chart only if
  // visible, `hidden`/`event_labels` only when set).
  function serializedLayout(): string {
    const out: Record<string, unknown> = { ...(metrics?.layout?.sources ?? {}) };
    for (const [source, ed] of Object.entries(edit) as [MarketingSource, SourceEditState][]) {
      const allDrilldowns = DRILLDOWNS[source] ?? [];
      const allKeys = ALL_METRICS[source] ?? [];
      // Only real metric keys — a mid-drag persist must never send the dnd shadow placeholder,
      // which the API's layout validation would refuse outright.
      const visible = ed.tiles.map((t) => t.id).filter((id) => allKeys.includes(id));
      const labels: Record<string, Record<string, string>> = {};
      for (const [key, l] of Object.entries(ed.labels)) {
        const entry = filledLabels(l);
        if (Object.keys(entry).length) labels[key] = entry;
      }
      const src: SourceLayout = {
        tiles: visible,
        labels,
        drilldowns: allDrilldowns.filter((k) => ed.drilldowns.includes(k)),
        chart_metric: ed.chart_metric && visible.includes(ed.chart_metric) ? ed.chart_metric : null,
      };
      if (ed.hidden) src.hidden = true;
      if (source === "ga4") {
        const eventLabels: Record<string, Record<string, string>> = {};
        for (const [key, l] of Object.entries(ed.event_labels)) {
          const entry = filledLabels(l);
          if (Object.keys(entry).length) eventLabels[key] = entry;
        }
        if (Object.keys(eventLabels).length) src.event_labels = eventLabels;
      }
      out[source] = src;
    }
    return JSON.stringify({ sources: out });
  }

  // Instant persist, like the My Day board: a hidden form posts the whole layout on every
  // meaningful change (drop, toggle, label blur) — no explicit save button to forget.
  let layoutForm: HTMLFormElement | undefined = $state();
  let layoutValue = $state("");
  function persist() {
    layoutValue = serializedLayout();
    setTimeout(() => layoutForm?.requestSubmit(), 0);
  }

  // ---- The comparison (#312) --------------------------------------------------------------
  // Dashboard-level, not per source: one screen where GA4 reads against last year and Search
  // Console against last month is not a screen anyone can summarise. `""` is "follow the org
  // default" and is a real stored state (null), not an empty field — which is why it posts as
  // its own value rather than being skipped.
  const compare = $derived(metrics?.compare ?? null);
  const comparedPeriod = $derived(compare ? comparePeriodLabel(compare) : "");
  // ---- The period (#316) --------------------------------------------------------------------
  // The tab row holds the rolling presets; the picker holds the named months and quarters. Both
  // are links, and both read their state out of the URL — nothing about the period lives in a
  // component. The picker's option list is anchored on the API's own last complete day, so the
  // browser's clock never decides which months exist.
  const currentPeriod = $derived(compare ? currentPeriodLabel(compare) : "");
  // Anchored on the tenant's own calendar rather than on the streamed payload, so the picker is
  // part of the shell: a control that appears a second after the page did reads as a glitch.
  const periodAnchor = anchorMonth();
  let compareForm: HTMLFormElement | undefined = $state();
  let compareValue = $state("");
  function persistCompare(event: Event) {
    compareValue = (event.currentTarget as HTMLSelectElement).value;
    setTimeout(() => compareForm?.requestSubmit(), 0);
  }
</script>

<form method="POST" action="?/saveLayout" use:enhance bind:this={layoutForm} class="hidden">
  <input type="hidden" name="company_id" value={companyId} />
  <input type="hidden" name="layout" value={layoutValue} />
</form>

<form method="POST" action="?/saveCompare" use:enhance bind:this={compareForm} class="hidden">
  <input type="hidden" name="company_id" value={companyId} />
  <input type="hidden" name="compare" value={compareValue} />
</form>

<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#each PERIOD_PRESETS as r (r)}
      <a href={urlFor(r, website)} class={rangeClass(range === r)} data-sveltekit-noscroll>
        {t(`marketing.range.${r}`)}
      </a>
    {/each}
    <MarketingPeriodPicker
      anchor={periodAnchor}
      active={range}
      label={currentPeriod}
      urlFor={(period) => urlFor(period, website)}
    />
  </div>
  {#if metrics?.can_manage && filteredByWebsite.length > 0}
    <button
      type="button"
      class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm {editMode
        ? 'border-brand text-brand'
        : 'border-border text-text hover:border-brand'}"
      aria-pressed={editMode}
      onclick={toggleEdit}
    >
      {#if editMode}
        <Check size={14} /> {t("marketing.layout.done")}
      {:else}
        <Pencil size={14} /> {t("marketing.layout.edit")}
      {/if}
    </button>
  {/if}
</div>

{#if websites.length > 0}
  <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    <a href={urlFor(range, "")} class={rangeClass(!website)} data-sveltekit-noscroll>
      {t("marketing.filter.all_websites")}
    </a>
    {#each websites as site (site.id)}
      <a
        href={urlFor(range, site.id)}
        class={rangeClass(website === site.id)}
        data-sveltekit-noscroll
      >
        {site.name}
      </a>
    {/each}
    {#if hasClientLevel}
      <a
        href={urlFor(range, "client")}
        class={rangeClass(website === "client")}
        data-sveltekit-noscroll
      >
        {t("marketing.website_group_none")}
      </a>
    {/if}
  </div>
{/if}

{#if currentPeriod}
  <!-- The span the numbers below actually cover, in words. A tab reading "Deze maand" says which
       month it *means*; a picked "Q3 2026" says nothing about where it ends until the 30th of
       September. Both are answered by naming the resolved dates (#316), which is also the only
       way a shared link can be checked by whoever receives it. -->
  <p class="mb-3 text-xs text-text-muted">
    {t("marketing.period.caption", { period: currentPeriod })}
  </p>
{/if}

{#if editMode}
  <!-- One switcher for every tile and key-event name below, not one per input (docs/UX.md). -->
  <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
    <p class="text-xs text-text-muted">{t("marketing.layout.edit_hint")}</p>
    <I18nLocaleSwitcher hint={false} />
  </div>

  <!-- The comparison is a property of this client's dashboard, so it is edited with the rest of
       it rather than hidden in Instellingen — and it persists on change like every other control
       here. Inheriting is an option, not a blank: the label says what it inherits. -->
  <div
    class="mb-4 flex flex-wrap items-end justify-between gap-3 rounded-xl border border-brand/40 bg-surface-raised p-4"
  >
    <div class="min-w-0">
      <label for="marketing-compare" class="mb-1 block text-sm font-medium text-text">
        {t("marketing.compare.label")}
      </label>
      <select
        id="marketing-compare"
        value={metrics?.compare_setting ?? ""}
        onchange={persistCompare}
        class="w-full min-w-64 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand"
      >
        <option value="">
          {t("marketing.compare.inherit", {
            mode: compareModeLabel(metrics?.compare_default ?? "year"),
          })}
        </option>
        {#each COMPARE_PERIODS as mode (mode)}
          <option value={mode}>{compareModeLabel(mode)}</option>
        {/each}
      </select>
    </div>
    <p class="max-w-md text-xs text-text-muted">
      {t("marketing.compare.hint")}
      {#if comparedPeriod}
        <span class="mt-1 block text-text"
          >{t("marketing.compare.caption", { period: comparedPeriod })}</span
        >
      {/if}
    </p>
  </div>
{/if}

{#if pending && !metrics}
  <div
    class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center text-sm text-text-muted"
  >
    {t("common.loading")}
  </div>
{:else if !metrics || sources.length === 0}
  <!-- One empty state, not two, and `needs_connection` no longer owns a branch of its own.
       It is a question about **Google**, and it used to short-circuit the whole screen: a client
       with two websites and an org with no Google grant read *"Koppel een Google-account"* over
       a dashboard that would happily have carried SE Ranking (an agency API key) and Rank Math (a
       per-website WordPress password), with no way from here to attach either (#399). This is
       CLAUDE.md's Cloudflare rule one module over — a health probe is evidence, never the gate.
       So it decides a *sentence* now, and the connect control is offered either way. -->
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    {#if !metrics?.can_manage}
      <p class="text-sm text-text-muted">
        {t(metrics?.needs_connection ? "marketing.empty.ask_admin" : "marketing.empty.no_links")}
      </p>
    {:else}
      <p class="text-sm text-text-muted">{t("marketing.empty.no_links")}</p>
      {#if onconnect}
        <button
          type="button"
          class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
          onclick={onconnect}
        >
          {t("marketing.connect.open")}
        </button>
      {:else}
        <a
          href={manageHref}
          class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
        >
          {t("marketing.manage_on_client")}
        </a>
      {/if}
      {#if metrics?.needs_connection}
        <!-- One consent for GA4 + Search Console + Ads, returning to this dashboard. Below the
             connect control rather than instead of it: three of the five sources need it and two
             do not, and only the picker knows which the reader is after. -->
        <p class="mt-4 text-sm text-text-muted">{t("marketing.empty.needs_connection")}</p>
        <a
          href={connectHref(page.url.pathname + page.url.search)}
          data-sveltekit-preload-data="off"
          class="mt-1 inline-block text-sm font-medium text-brand hover:underline"
        >
          {t("marketing.connect_cta")}
        </a>
      {/if}
    {/if}
  </div>
{:else}
  <div class="space-y-6">
    {#each groups as group (group.id ?? "_company")}
      <section>
        {#if showGroupHeadings}
          <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-text-muted">
            {group.name ?? t("marketing.website_group_none")}
          </h2>
        {/if}
        <div class="space-y-5">
          {#each group.sources as src (src.link_id)}
            <MarketingSourceSection
              {companyId}
              {src}
              period={range}
              {compare}
              edit={editMode ? (edit[src.source] ?? null) : null}
              onchange={persist}
            />
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}
