<script lang="ts">
  /**
   * The portal homepage's marketing widget (#254): the client's marketing dashboard exactly as
   * the agency curated it (#192/#193 — the API strips hidden tiles server-side and a client
   * holds no `marketing.link.manage`, so the *content* is locked). What the client does control
   * is the tile itself: the portal board lets them add, remove and reorder widgets like staff
   * My Day, and this is one of them. Data is URL-driven (company/website switchers), so the
   * portal page injects it; the spec's own `load` stays a no-op.
   */
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  import MarketingSourceSection from "./MarketingSourceSection.svelte";
  import type { CompanyMarketing } from "./types";

  let { data }: { data: unknown } = $props();

  interface PortalMarketing {
    companyId: string | null;
    website: string;
    metrics: CompanyMarketing | null;
  }
  const portal = $derived(
    (data ?? { companyId: null, website: "", metrics: null }) as PortalMarketing,
  );
  const websites = $derived(portal.metrics?.websites ?? []);
  // A client with several websites always sees them named and switchable (owner feedback);
  // the default view shows everything, client-level links get their own tab.
  const hasClientLevel = $derived((portal.metrics?.sources ?? []).some((s) => !s.website_id));
  const sources = $derived(
    (portal.metrics?.sources ?? []).filter((s) =>
      !portal.website
        ? true
        : portal.website === "client"
          ? !s.website_id
          : s.website_id === portal.website,
    ),
  );
</script>

<DashboardWidgetCard title={t("dashboard.widget.marketing.portal")}>
  {#if websites.length > 1}
    <!-- Several websites: always name them, switchable (owner feedback). -->
    <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
      <a
        href={`?company=${portal.companyId}`}
        data-sveltekit-noscroll
        class="rounded-lg px-3 py-1.5 text-sm font-medium {!portal.website
          ? 'bg-brand text-white'
          : 'text-text-muted hover:bg-surface'}"
      >
        {t("marketing.filter.all_websites")}
      </a>
      {#each websites as site (site.id)}
        <a
          href={`?company=${portal.companyId}&website=${site.id}`}
          data-sveltekit-noscroll
          class="rounded-lg px-3 py-1.5 text-sm font-medium {portal.website === site.id
            ? 'bg-brand text-white'
            : 'text-text-muted hover:bg-surface'}"
        >
          {site.name}
        </a>
      {/each}
      {#if hasClientLevel}
        <a
          href={`?company=${portal.companyId}&website=client`}
          data-sveltekit-noscroll
          class="rounded-lg px-3 py-1.5 text-sm font-medium {portal.website === 'client'
            ? 'bg-brand text-white'
            : 'text-text-muted hover:bg-surface'}"
        >
          {t("marketing.website_group_none")}
        </a>
      {/if}
    </div>
  {/if}
  {#if sources.length > 0}
    <div class="space-y-5">
      {#each sources as src (src.link_id)}
        <MarketingSourceSection companyId={portal.companyId ?? ""} {src} rangeDays={30} />
      {/each}
    </div>
  {:else}
    <p class="text-sm text-text-muted">{t("portal.home.no_data")}</p>
  {/if}
</DashboardWidgetCard>
