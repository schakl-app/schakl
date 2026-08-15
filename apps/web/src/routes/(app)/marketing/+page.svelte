<script lang="ts">
  /**
   * Top-level Marketing page (epic #134): pick a client, then work with the same marketing
   * dashboard the client's own tab shows — one shared component, so reading and curating
   * (edit mode, #192) are available in both places identically.
   *
   * The picker is a grid of tiles over the clients that have a source linked, not a dropdown
   * over every company: see `MarketingClientTiles`.
   */
  import { Plus } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import MarketingClientTiles from "$lib/modules/marketing/MarketingClientTiles.svelte";
  import MarketingConnectDialog from "$lib/modules/marketing/MarketingConnectDialog.svelte";
  import MarketingDashboard from "$lib/modules/marketing/MarketingDashboard.svelte";
  import { ALL_SOURCES } from "$lib/modules/marketing/types";
  import type { CompanyMarketing, MarketingClientRow } from "$lib/modules/marketing/types";

  let { data, form } = $props();

  // The ＋ this page never had (#338). Its empty state used to say "koppel een bron op de
  // klantpagina" and link to the whole client list, leaving both halves of the job — find the
  // client, then find the gesture, which lives behind ⋯ → Bewerken — as an exercise.
  let connecting = $state(false);

  const clients = $derived(data.clients as MarketingClientRow[]);
  // A client reached by a link (or a bookmark) need not be one of the tiles — a source can be
  // unlinked after the URL was shared — so the name is a nicety here, never the gate.
  const selectedName = $derived(
    clients.find((c) => c.company_id === data.companyId)?.company_name ?? "",
  );
  // Resolved into `$state`, never awaited in the markup: a raw `{#await}` re-enters its pending
  // branch on every invalidation, and edit mode holds unsaved tile names its own save would then
  // discard (docs/PERFORMANCE.md).
  let marketing = $state<CompanyMarketing | null>(null);
  let pending = $state(true);
  $effect(() => {
    const promise = data.metrics;
    pending = true;
    void promise.then((value) => {
      // A stale resolution loses: picking two clients quickly leaves two loads in flight.
      if (data.metrics !== promise) return;
      marketing = value as CompanyMarketing | null;
      pending = false;
    });
  });

  function urlFor(companyId: string, range: string, website: string): string {
    const params = new URLSearchParams();
    if (companyId) params.set("company", companyId);
    if (range && range !== "30d") params.set("range", range);
    if (website) params.set("website", website);
    const qs = params.toString();
    return qs ? `/marketing?${qs}` : "/marketing";
  }

  // A website belongs to one client, so the filter resets with the client.
  const clientHref = (companyId: string) => urlFor(companyId, data.range, "");
</script>

<svelte:head>
  <title>{pageTitle(navLabel("marketing", t("nav.marketing")))}</title>
</svelte:head>

<div class="mb-4 flex items-start justify-between gap-4">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("marketing", t("nav.marketing"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("marketing.page.subtitle")}</p>
  </div>
  {#if data.canLink}
    <button
      type="button"
      class="inline-flex shrink-0 items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
      onclick={() => (connecting = true)}
    >
      <Plus size={15} aria-hidden="true" />
      {t("marketing.connect.open")}
    </button>
  {/if}
</div>

{#if !data.companyId}
  <MarketingClientTiles
    rows={clients}
    total={data.clientsTotal}
    hrefFor={clientHref}
    onconnect={data.canLink ? () => (connecting = true) : undefined}
  />
{:else}
  <div class="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
    <a href={urlFor("", data.range, "")} class="text-sm text-text-muted hover:text-text">
      ← {t("marketing.clients.all")}
    </a>
    <a href={`/companies/${data.companyId}`} class="text-sm text-text-muted hover:text-text">
      {selectedName || t("marketing.clients.open_company")} ↗
    </a>
  </div>

  <MarketingDashboard
    companyId={data.companyId}
    metrics={marketing}
    {pending}
    range={data.range}
    website={data.website}
    urlFor={(range, website) => urlFor(data.companyId, range, website)}
    manageHref={`/companies/${data.companyId}`}
  />
{/if}

{#if data.canLink}
  <MarketingConnectDialog
    bind:open={connecting}
    locale={data.locale}
    sources={ALL_SOURCES}
    title={t("marketing.connect.open")}
    error={form?.error ?? null}
    qcError={form?.qcError ?? null}
    inlineCreated={form?.inlineCreated ?? null}
  />
{/if}
