<script lang="ts">
  import { Plus } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import MarketingConnectDialog from "$lib/modules/marketing/MarketingConnectDialog.svelte";
  import MarketingDashboard from "$lib/modules/marketing/MarketingDashboard.svelte";
  import { ALL_SOURCES } from "$lib/modules/marketing/types";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data, form } = $props();

  // The ＋ this tab never had (#399). Its only empty state said "koppel een bron op de
  // klantpagina" — or, with no Google grant anywhere in the org, an unconditional "Google
  // koppelen" over a client whose SE Ranking key and Rank Math password were already set up.
  // The client is the route here, so the dialog asks only which account.
  let connecting = $state(false);

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

  // Already linked here, so the pickers do not offer an account twice.
  const linkedIds = $derived(
    Object.fromEntries(
      ALL_SOURCES.map((s) => [
        s,
        (marketing?.sources ?? []).filter((x) => x.source === s).map((x) => x.external_id),
      ]),
    ),
  );

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

<div class="mb-6 flex items-start justify-between gap-4">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("marketing.tab.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("marketing.subtitle")}</p>
  </div>
  {#if data.canLink}
    <button
      type="button"
      class="mt-2 inline-flex shrink-0 items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
      onclick={() => (connecting = true)}
    >
      <Plus size={15} aria-hidden="true" />
      {t("marketing.connect.open")}
    </button>
  {/if}
</div>

<MarketingDashboard
  companyId={company.id}
  metrics={marketing}
  {pending}
  range={data.range}
  website={data.website}
  {urlFor}
  manageHref={`/companies/${company.id}`}
  onconnect={data.canLink ? () => (connecting = true) : undefined}
/>

{#if data.canLink}
  <!-- Websites are handed down rather than fetched: the metrics payload already carries them for
       the group headings above, so the site-key picker costs this screen nothing. `null` while
       the payload is still streaming means "ask", which is the honest state — a hardcoded `[]`
       is what made the Rank Math row say "deze klant heeft nog geen website" (#399). -->
  <MarketingConnectDialog
    bind:open={connecting}
    companyId={company.id}
    websites={marketing?.websites ?? null}
    sources={ALL_SOURCES}
    {linkedIds}
    title={t("marketing.connect.open")}
    error={form?.error ?? null}
  />
{/if}
