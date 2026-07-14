<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingSourceSection from "$lib/modules/marketing/MarketingSourceSection.svelte";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data } = $props();
  const company = $derived(data.company);
  const marketing = $derived(data.metrics as CompanyMarketing | null);
  const sources = $derived(marketing?.sources ?? []);

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
  <h1 class="mt-2 text-xl font-semibold text-text">{t("marketing.tab.title")}</h1>
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
  <div class="space-y-5">
    {#each sources as src (src.link_id)}
      <MarketingSourceSection companyId={company.id} {src} rangeDays={data.rangeDays} />
    {/each}
  </div>
{/if}
