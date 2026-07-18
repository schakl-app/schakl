<script lang="ts">
  import { Check, Pencil } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingLayoutEditor from "$lib/modules/marketing/MarketingLayoutEditor.svelte";
  import MarketingSourceSection from "$lib/modules/marketing/MarketingSourceSection.svelte";
  import type { CompanyMarketing, SourceMetrics } from "$lib/modules/marketing/types";

  let { data } = $props();

  // Use vs edit mode (docs/UX.md, #192): the pencil turns the curated tab into its editor —
  // reorder/hide/relabel tiles per source, relabel key events, hide a whole source. Gated on
  // marketing.link.manage (can_manage). Declared here so `sources` can read it below.
  let editMode = $state(false);
  let editingSource = $state<string | null>(null);

  const company = $derived(data.company);
  const marketing = $derived(data.metrics as CompanyMarketing | null);
  const allSources = $derived(marketing?.sources ?? []);
  // Website filter (owner feedback): marketing reads per site. "" shows everything,
  // "client" narrows to client-level links, a website id narrows to that site.
  const filteredByWebsite = $derived(
    allSources.filter((s) =>
      !data.website ? true : data.website === "client" ? !s.website_id : s.website_id === data.website,
    ),
  );
  // A hidden source (#192) is only present in the payload for a manager; it shows in edit mode
  // (with a re-enable toggle) but stays out of the read/use view, matching what the client sees.
  const sources = $derived(filteredByWebsite.filter((s) => editMode || !s.hidden));
  const websites = $derived(marketing?.websites ?? []);
  const hasClientLevel = $derived(allSources.some((s) => !s.website_id));

  function urlFor(range: string, website = data.website): string {
    const params = new URLSearchParams();
    if (range && range !== "30d") params.set("range", range);
    if (website) params.set("website", website);
    const qs = params.toString();
    return qs ? `?${qs}` : `/companies/${company.id}/marketing`;
  }

  // Group the sources per client website (a client with two sites reads per site); links
  // without a website stay in a trailing client-level group. One flat list when nothing is
  // attached to a website — exactly the pre-existing view.
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
</script>

<svelte:head>
  <title>{pageTitle(`${company.name} · ${t("marketing.tab.title")}`)}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
    <h1 class="text-xl font-semibold text-text">{t("marketing.tab.title")}</h1>
    {#if marketing?.can_manage && filteredByWebsite.length > 0}
      <button
        type="button"
        class="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm text-text hover:border-brand"
        aria-pressed={editMode}
        onclick={() => {
          editMode = !editMode;
          editingSource = null;
        }}
      >
        {#if editMode}
          <Check size={14} /> {t("marketing.layout.done")}
        {:else}
          <Pencil size={14} /> {t("marketing.layout.edit")}
        {/if}
      </button>
    {/if}
  </div>
  <p class="mt-1 text-sm text-text-muted">{t("marketing.subtitle")}</p>
</div>

<div class="mb-3 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
  {#each RANGES as r (r)}
    <a href={urlFor(r)} class={rangeClass(data.range === r)} data-sveltekit-noscroll>
      {t(`marketing.range.${r}`)}
    </a>
  {/each}
</div>

{#if websites.length > 0}
  <div class="mb-5 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    <a href={urlFor(data.range, "")} class={rangeClass(!data.website)} data-sveltekit-noscroll>
      {t("marketing.filter.all_websites")}
    </a>
    {#each websites as site (site.id)}
      <a
        href={urlFor(data.range, site.id)}
        class={rangeClass(data.website === site.id)}
        data-sveltekit-noscroll
      >
        {site.name}
      </a>
    {/each}
    {#if hasClientLevel}
      <a
        href={urlFor(data.range, "client")}
        class={rangeClass(data.website === "client")}
        data-sveltekit-noscroll
      >
        {t("marketing.website_group_none")}
      </a>
    {/if}
  </div>
{/if}

{#if !marketing || sources.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("marketing.empty.no_links")}</p>
    <a
      href={`/companies/${company.id}`}
      class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
    >
      ← {company.name}
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
            {#if editMode && editingSource === src.source}
              <MarketingLayoutEditor
                companyId={company.id}
                linkId={src.link_id}
                source={src.source}
                layout={marketing.layout}
                ondone={() => (editingSource = null)}
              />
            {:else}
              <div class="relative {editMode && src.hidden ? 'opacity-60' : ''}">
                {#if editMode}
                  <div class="absolute right-4 top-4 z-10 flex items-center gap-1.5">
                    {#if src.hidden}
                      <span class="rounded-lg bg-amber-100 px-2 py-1 text-xs font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                        {t("marketing.layout.source_hidden")}
                      </span>
                    {/if}
                    <button
                      type="button"
                      class="flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-text hover:border-brand"
                      onclick={() => (editingSource = src.source)}
                    >
                      <Pencil size={13} />
                      {t("marketing.layout.edit_source")}
                    </button>
                  </div>
                {/if}
                <MarketingSourceSection companyId={company.id} {src} rangeDays={data.rangeDays} />
              </div>
            {/if}
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}
