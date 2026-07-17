<script lang="ts">
  /**
   * Top-level Marketing page (epic #134): pick a client, then read its Analytics, Search Console
   * and Ads data as three sections. Reuses the per-source section the client tab renders; the
   * client picker + range live in the URL so the SSR load fetches exactly what's shown.
   */
  import { goto } from "$app/navigation";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import MarketingSourceSection from "$lib/modules/marketing/MarketingSourceSection.svelte";
  import type { CompanyMarketing, MarketingSource } from "$lib/modules/marketing/types";

  let { data } = $props();

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const selectedName = $derived(data.companies.find((c) => c.id === data.companyId)?.name ?? "");
  const marketing = $derived(data.metrics as CompanyMarketing | null);

  // Analytics → Search Console → Ads, a stable order regardless of link creation order.
  const ORDER: Record<MarketingSource, number> = { ga4: 0, gsc: 1, gads: 2 };
  const allSources = $derived(
    [...(marketing?.sources ?? [])].sort((a, b) => ORDER[a.source] - ORDER[b.source]),
  );
  // Website filter: marketing is read per site (owner feedback) — "" shows everything,
  // "client" narrows to client-level links, a website id narrows to that site's links.
  const sources = $derived(
    allSources.filter((s) =>
      !data.website ? true : data.website === "client" ? !s.website_id : s.website_id === data.website,
    ),
  );
  const websites = $derived(marketing?.websites ?? []);
  const hasClientLevel = $derived(allSources.some((s) => !s.website_id));

  function urlFor(companyId: string, range: string, website = data.website): string {
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

  const RANGES = ["30d", "month", "quarter", "90d", "yoy"] as const;
  const rangeClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<svelte:head>
  <title>{pageTitle(t("nav.marketing"))}</title>
</svelte:head>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{t("nav.marketing")}</h1>
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
  <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
    <a
      href={`/companies/${data.companyId}`}
      class="text-sm text-text-muted hover:text-text"
    >
      {selectedName} ↗
    </a>
    <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
      {#each RANGES as r (r)}
        <a href={urlFor(data.companyId, r)} class={rangeClass(data.range === r)} data-sveltekit-noscroll>
          {t(`marketing.range.${r}`)}
        </a>
      {/each}
    </div>
  </div>

  {#if websites.length > 0}
    <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
      <a
        href={urlFor(data.companyId, data.range, "")}
        class={rangeClass(!data.website)}
        data-sveltekit-noscroll
      >
        {t("marketing.filter.all_websites")}
      </a>
      {#each websites as site (site.id)}
        <a
          href={urlFor(data.companyId, data.range, site.id)}
          class={rangeClass(data.website === site.id)}
          data-sveltekit-noscroll
        >
          {site.name}
        </a>
      {/each}
      {#if hasClientLevel}
        <a
          href={urlFor(data.companyId, data.range, "client")}
          class={rangeClass(data.website === "client")}
          data-sveltekit-noscroll
        >
          {t("marketing.website_group_none")}
        </a>
      {/if}
    </div>
  {/if}

  {#if marketing?.needs_connection}
    <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-sm text-text-muted">
      {#if marketing.can_manage}
        <p>{t("marketing.empty.needs_connection")}</p>
      {:else}
        <p>{t("marketing.empty.ask_admin")}</p>
      {/if}
    </div>
  {:else if sources.length === 0}
    <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
      <p class="text-sm text-text-muted">{t("marketing.empty.no_links")}</p>
      <a
        href={`/companies/${data.companyId}`}
        class="mt-2 inline-block text-sm font-medium text-brand hover:underline"
      >
        {t("marketing.manage_on_client")}
      </a>
    </div>
  {:else}
    <div class="space-y-5">
      {#each sources as src (src.link_id)}
        <MarketingSourceSection companyId={data.companyId} {src} rangeDays={data.rangeDays} />
      {/each}
    </div>
  {/if}
{/if}
