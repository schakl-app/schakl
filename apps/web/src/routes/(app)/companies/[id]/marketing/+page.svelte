<script lang="ts">
  import { Check, Pencil } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingLayoutEditor from "$lib/modules/marketing/MarketingLayoutEditor.svelte";
  import MarketingSourceSection from "$lib/modules/marketing/MarketingSourceSection.svelte";
  import type { CompanyMarketing, SourceMetrics } from "$lib/modules/marketing/types";

  let { data } = $props();
  const company = $derived(data.company);
  const marketing = $derived(data.metrics as CompanyMarketing | null);
  const sources = $derived(marketing?.sources ?? []);

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

  // Use vs edit mode (docs/UX.md, #192): the pencil turns the curated tab into its editor —
  // reorder/hide/relabel tiles per source. Gated on marketing.link.manage (can_manage).
  let editMode = $state(false);
  let editingSource = $state<string | null>(null);

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
  <a href={`/companies/${company.id}`} class="text-sm text-text-muted hover:text-text">
    ← {company.name}
  </a>
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
    <h1 class="text-xl font-semibold text-text">{t("marketing.tab.title")}</h1>
    {#if marketing?.can_manage && sources.length > 0}
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

<div class="mb-5 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
  {#each RANGES as r (r)}
    <a href={`?range=${r}`} class={rangeClass(data.range === r)} data-sveltekit-noscroll>
      {t(`marketing.range.${r}`)}
    </a>
  {/each}
</div>

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
                source={src.source}
                layout={marketing.layout}
                ondone={() => (editingSource = null)}
              />
            {:else}
              <div class="relative">
                {#if editMode}
                  <button
                    type="button"
                    class="absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-text hover:border-brand"
                    onclick={() => (editingSource = src.source)}
                  >
                    <Pencil size={13} />
                    {t("marketing.layout.edit_source")}
                  </button>
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
