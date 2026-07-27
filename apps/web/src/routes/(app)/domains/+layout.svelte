<script lang="ts">
  /**
   * Sub-route tabs over the domains section (#250): the domain list and the TLD price
   * list — the submenu-tabs convention (docs/UX.md, Navigation), same shape as
   * /subscriptions. Viewers without the price permission get no tab row: a single tab is
   * noise, and the list page is then the whole section.
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { navLabel } from "$lib/core/title";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canRead = $derived(can(page.data.user, "domains.domain.read"));
  const canPrices = $derived(can(page.data.user, "domains.tld_price.read"));
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

{#if canPrices}
  <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#if canRead}
      <!-- A domain detail page is still the Domeinen surface, so the tab stays lit there. -->
      <a
        href="/domains"
        class={tabClass(path.startsWith("/domains") && !path.startsWith("/domains/tld-prices"))}
      >
        {navLabel("domains", t("domains.title"))}
      </a>
    {/if}
    <a href="/domains/tld-prices" class={tabClass(path.startsWith("/domains/tld-prices"))}>
      {t("domains.tld_prices.tab")}
    </a>
  </div>
{/if}

{@render children()}
