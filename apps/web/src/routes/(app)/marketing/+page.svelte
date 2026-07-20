<script lang="ts">
  /**
   * Top-level Marketing page (epic #134): pick a client, then work with the same marketing
   * dashboard the client's own tab shows — one shared component, so reading and curating
   * (edit mode, #192) are available in both places identically.
   */
  import { goto } from "$app/navigation";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import MarketingDashboard from "$lib/modules/marketing/MarketingDashboard.svelte";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data } = $props();

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const selectedName = $derived(data.companies.find((c) => c.id === data.companyId)?.name ?? "");
  const marketing = $derived(data.metrics as CompanyMarketing | null);

  function urlFor(companyId: string, range: string, website: string): string {
    const params = new URLSearchParams();
    if (companyId) params.set("company", companyId);
    if (range && range !== "30d") params.set("range", range);
    if (website) params.set("website", website);
    const qs = params.toString();
    return qs ? `/marketing?${qs}` : "/marketing";
  }

  function pickCompany(id: string) {
    // A website belongs to one client, so the filter resets with the client.
    goto(urlFor(id, data.range, ""), { keepFocus: true, noScroll: true });
  }
</script>

<svelte:head>
  <title>{pageTitle(navLabel("marketing", t("nav.marketing")))}</title>
</svelte:head>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{navLabel("marketing", t("nav.marketing"))}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("marketing.page.subtitle")}</p>
</div>

<div class="mb-5 max-w-md">
  <label for="marketing-client" class="mb-1 block text-xs font-medium text-text-muted">
    {t("marketing.select_client")}
  </label>
  <Combobox
    items={companyItems}
    name="_marketing_client"
    id="marketing-client"
    value={data.companyId}
    placeholder={t("marketing.select_client")}
    onselect={pickCompany}
  />
</div>

{#if !data.companyId}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("marketing.page.pick_prompt")}</p>
  </div>
{:else}
  <div class="mb-3">
    <a href={`/companies/${data.companyId}`} class="text-sm text-text-muted hover:text-text">
      {selectedName} ↗
    </a>
  </div>

  <MarketingDashboard
    companyId={data.companyId}
    metrics={marketing}
    range={data.range}
    rangeDays={data.rangeDays}
    website={data.website}
    urlFor={(range, website) => urlFor(data.companyId, range, website)}
    manageHref={`/companies/${data.companyId}`}
  />
{/if}
