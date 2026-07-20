<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingDashboard from "$lib/modules/marketing/MarketingDashboard.svelte";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data } = $props();

  const company = $derived(data.company);
  const marketing = $derived(data.metrics as CompanyMarketing | null);

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
  range={data.range}
  rangeDays={data.rangeDays}
  website={data.website}
  {urlFor}
  manageHref={`/companies/${company.id}`}
/>
