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
  import { t } from "$lib/core/i18n";

  import MarketingSourceSection from "./MarketingSourceSection.svelte";
  import {
    ALL_METRICS,
    DRILLDOWNS,
    type CompanyMarketing,
    type MarketingSource,
    type SourceEditState,
    type SourceLayout,
    type SourceMetrics,
  } from "./types";

  let {
    companyId,
    metrics,
    range,
    rangeDays,
    website,
    urlFor,
    manageHref,
  }: {
    companyId: string;
    metrics: CompanyMarketing | null;
    range: string;
    rangeDays: number;
    website: string;
    /** Builds the page's own URL for a range/website pick (the two hosts differ in query shape). */
    urlFor: (range: string, website: string) => string;
    /** Where the empty state sends the user to link accounts (the client page). */
    manageHref: string;
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

  const RANGES = ["30d", "month", "quarter", "90d", "yoy"] as const;
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

  function seedEdit(): Partial<Record<MarketingSource, SourceEditState>> {
    const out: Partial<Record<MarketingSource, SourceEditState>> = {};
    for (const src of allSources) {
      if (out[src.source]) continue; // two links of one source share one layout entry
      const all = ALL_METRICS[src.source] ?? [];
      const stored: SourceLayout = metrics?.layout?.sources?.[src.source] ?? {};
      const visible = (stored.tiles ?? all).filter((k) => all.includes(k));
      const labels: Record<string, { nl: string; en: string }> = {};
      for (const key of all) {
        labels[key] = {
          nl: stored.labels?.[key]?.nl ?? "",
          en: stored.labels?.[key]?.en ?? "",
        };
      }
      const event_labels: Record<string, { nl: string; en: string }> = {};
      for (const [key, l] of Object.entries(stored.event_labels ?? {})) {
        event_labels[key] = { nl: l.nl ?? "", en: l.en ?? "" };
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
        const entry: Record<string, string> = {};
        if (l.nl.trim()) entry.nl = l.nl.trim();
        if (l.en.trim()) entry.en = l.en.trim();
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
          const entry: Record<string, string> = {};
          if (l.nl.trim()) entry.nl = l.nl.trim();
          if (l.en.trim()) entry.en = l.en.trim();
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
</script>

<form method="POST" action="?/saveLayout" use:enhance bind:this={layoutForm} class="hidden">
  <input type="hidden" name="company_id" value={companyId} />
  <input type="hidden" name="layout" value={layoutValue} />
</form>

<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#each RANGES as r (r)}
      <a href={urlFor(r, website)} class={rangeClass(range === r)} data-sveltekit-noscroll>
        {t(`marketing.range.${r}`)}
      </a>
    {/each}
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

{#if editMode}
  <p class="mb-4 text-xs text-text-muted">{t("marketing.layout.edit_hint")}</p>
{/if}

{#if metrics?.needs_connection}
  <div
    class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-sm text-text-muted"
  >
    {#if metrics.can_manage}
      <p>{t("marketing.empty.needs_connection")}</p>
    {:else}
      <p>{t("marketing.empty.ask_admin")}</p>
    {/if}
  </div>
{:else if !metrics || sources.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("marketing.empty.no_links")}</p>
    <a href={manageHref} class="mt-2 inline-block text-sm font-medium text-brand hover:underline">
      {t("marketing.manage_on_client")}
    </a>
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
              {rangeDays}
              edit={editMode ? (edit[src.source] ?? null) : null}
              onchange={persist}
            />
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}
