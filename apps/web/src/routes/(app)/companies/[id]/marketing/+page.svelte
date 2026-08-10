<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingDashboard from "$lib/modules/marketing/MarketingDashboard.svelte";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data } = $props();

  const company = $derived(data.company);
  // The metrics stream in behind the shell (docs/PERFORMANCE.md). Resolved into `$state` rather
  // than awaited in the markup: a raw `{#await}` falls back to its pending branch on every
  // invalidation, and this dashboard's edit mode holds unsaved tile names and orders that a
  // `?/saveLayout` round-trip would then throw away.
  let marketing = $state<CompanyMarketing | null>(null);
  let pending = $state(true);
  $effect(() => {
    const promise = data.metrics;
    pending = true;
    void promise.then((value) => {
      // Ignore a resolution the user has already navigated away from — the period tabs are links,
      // so a quick second click can land two in-flight loads out of order.
      if (data.metrics !== promise) return;
      marketing = value as CompanyMarketing | null;
      pending = false;
    });
  });

  function urlFor(range: string, website: string): string {
    const params = new URLSearchParams();
    if (range && range !== "30d") params.set("range", range);
    if (website) params.set("website", website);
    const qs = params.toString();
    return qs ? `?${qs}` : `/companies/${company.id}/marketing`;
  }
</script>

<svelte:head>
  <title>{pageTitle(`${company.name} · ${t("marketing.tab.title")}`)}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("marketing.tab.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("marketing.subtitle")}</p>
</div>

<MarketingDashboard
  companyId={company.id}
  metrics={marketing}
  {pending}
  range={data.range}
  website={data.website}
  {urlFor}
  manageHref={`/companies/${company.id}`}
/>
