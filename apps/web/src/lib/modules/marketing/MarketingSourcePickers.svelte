<script lang="ts">
  /**
   * The connect surface itself: which website, then one picker per source, then the connections.
   *
   * It exists because there were **two** copies of it and they disagreed (#399). The client
   * panel's edit mode rendered the website `<select>` and passed the picker a real `websiteId`
   * and a real `hasWebsites`; the connect dialog (#338) mounted the same picker with
   * `hasWebsites` written in as a literal false, and no site select at all. So on every screen
   * but one, the Rank Math row read *"deze klant heeft nog geen website"* for a client with
   * two, and there was nothing on the dialog to correct it with. Two copies of one question is
   * how one of them stops being asked.
   *
   * So there is one copy, both hosts mount it, and `hasWebsites` is *derived* here and nowhere
   * else. Whatever a sixth source needs, it needs in one file.
   *
   * The connections group (#411) is the second half. Tag Manager is something an agency attaches
   * to a client and it draws no numbers, so it is not a `MarketingSource` — see `types.ts` — and
   * the module may not import the integration's component to mount it (§6). Each integration
   * registers a `MarketingConnectorSpec` instead and this composes them, the way the company hub
   * composes panels it knows nothing about.
   */
  import { page } from "$app/state";

  import { t } from "$lib/core/i18n";
  import { marketingConnectorsFor } from "$lib/core/registry";

  import MarketingAccountPicker from "./MarketingAccountPicker.svelte";
  import { ALL_SOURCES, SITE_KEY_SOURCES, type MarketingSource } from "./types";

  let {
    companyId = "",
    websites = [],
    sources = ALL_SOURCES,
    linkedIds = {},
    action = "?/marketingLink",
    gtmAction = "?/gtmLink",
    connectors = true,
    error = null,
  }: {
    /** The client, when the host action cannot read it off the route (docs/UX.md). */
    companyId?: string;
    /** This client's websites. `[]` means the client has none — a real state, see below. */
    websites?: { id: string; name: string }[];
    /** Which sources to offer. Both hosts pass `ALL_SOURCES`; `/marketing/google-ads` narrows. */
    sources?: MarketingSource[];
    /** Per source, the external ids already linked here — filtered out of the options. */
    linkedIds?: Partial<Record<MarketingSource, string[]>>;
    /** The host page's form action for a marketing link. */
    action?: string;
    /** The host page's form action for a contributed connection (`gtmActions`). */
    gtmAction?: string;
    /** Narrowed hosts (`/marketing/google-ads`) offer one source and no connections. */
    connectors?: boolean;
    /** The host page's `form?.error`, so a refused link is read where it was asked for. */
    error?: string | null;
  } = $props();

  // Whether a *site* is part of the answer at all. Derived from the offered sources rather than
  // from the full list: a host narrowed to Ads must not grow a website select it cannot use.
  const needsWebsite = $derived(sources.some((s) => SITE_KEY_SOURCES.includes(s)));
  const hasWebsites = $derived(websites.length > 0);

  // A client with exactly one website gets it preselected — that is where the property belongs,
  // and making somebody choose between one option is a question with no information in it.
  //
  // Re-seeded when the *list* changes, never on every render: the client above this can change
  // under the dialog, and carrying the old client's site over would leave the select bound to a
  // value that is not one of its options — which renders as blank, not as "Hele klant". Keyed on
  // the ids rather than on a `touched` flag so a stable list never overrides a choice already
  // made. `seeded` is a plain variable, not `$state`: reading it here would make this effect's
  // own write re-run it.
  let picked = $state("");
  let seeded = "\u0000";
  $effect(() => {
    const key = websites.map((w) => w.id).join(",");
    if (key === seeded) return;
    seeded = key;
    picked = websites.length === 1 ? websites[0].id : "";
  });
  const websiteId = $derived(websites.some((w) => w.id === picked) ? picked : "");

  const gtmConnectors = $derived(
    connectors ? marketingConnectorsFor(page.data.theme?.enabledModules ?? [], page.data.user) : [],
  );
</script>

<div class="space-y-4">
  {#if error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}

  {#if hasWebsites}
    <!-- New links attach to a specific client website, so a client with several sites keeps each
         property with its own site; "whole client" stays available. Required only for a site-key
         source, which is why the hint below names it rather than the label. -->
    <div class="max-w-xs">
      <label for="marketing-link-website" class="mb-1 block text-xs font-medium text-text-muted">
        {t("marketing.link_website")}
      </label>
      <select
        id="marketing-link-website"
        bind:value={picked}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand"
      >
        <option value="">{t("marketing.link_website_all")}</option>
        {#each websites as site (site.id)}
          <option value={site.id}>{site.name}</option>
        {/each}
      </select>
      {#if needsWebsite && !websiteId}
        <p class="mt-1 text-xs text-text-muted">{t("marketing.link_website_hint")}</p>
      {/if}
    </div>
  {/if}

  <div class="grid gap-4 sm:grid-cols-3">
    {#each sources as source (source)}
      <MarketingAccountPicker
        {source}
        {action}
        {companyId}
        {websiteId}
        {hasWebsites}
        linkedIds={linkedIds[source] ?? []}
      />
    {/each}
  </div>

  {#each gtmConnectors as connector (connector.kind)}
    <!-- A connection is not a source: no KPI row, no daily numbers, its own module's route. It
         sits under its own rule rather than beside the five, so nobody reads it as a sixth
         dashboard section that never fills in (#411). -->
    {@const Connector = connector.component}
    <div class="space-y-1.5 border-t border-border pt-4">
      <span class="text-xs font-medium text-text-muted">{t(connector.labelKey)}</span>
      <Connector action={gtmAction} {companyId} connectNext={page.url.pathname + page.url.search} />
    </div>
  {/each}
</div>
