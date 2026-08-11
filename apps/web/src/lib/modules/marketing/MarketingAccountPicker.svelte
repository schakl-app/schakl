<script lang="ts">
  /**
   * One source's account picker (issue #132). Options come from Google, so they load lazily when
   * the picker mounts (in the panel's edit mode) via the `/marketing/accounts` proxy, never on the
   * company page's render. An empty list *teaches*: no connection → connect; missing scope →
   * reconnect; Ads with no developer token → say so. Selecting an account posts to the host page's
   * `?/marketingLink` action. **No inline-create** — these are external resources, the documented
   * picker exception (docs/UX.md), like invited employees.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";

  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import {
    connectHref,
    SITE_KEY_SOURCES,
    type AccountsResponse,
    type MarketingSource,
  } from "./types";

  let {
    source,
    linkedIds,
    websiteId = "",
    hasWebsites = true,
  }: {
    source: MarketingSource;
    /** external_ids already linked to this company for this source — filtered out of options. */
    linkedIds: string[];
    /** The client website the new link attaches to ("" = client-level). */
    websiteId?: string;
    /** Whether this client has any website at all — the two "no site" states differ (see below). */
    hasWebsites?: boolean;
  } = $props();

  // A site-key source (Rank Math) reads one client website's WordPress, so the site is not a
  // filter on the answer — it is what makes the question askable. Asking without one would spend
  // a round trip to be told `configured=false`, which the picker would then draw as "not set up
  // yet": a wrong diagnosis of a state the browser could already see.
  const needsWebsite = $derived(SITE_KEY_SOURCES.includes(source));
  const missingWebsite = $derived(needsWebsite && !websiteId);

  // One consent covers all three sources, and it comes back *here* — this picker is where the
  // gap was noticed, so it is where the user expects to land with it closed.
  const connect = $derived(connectHref(page.url.pathname + page.url.search));

  let loading = $state(true);
  let response = $state<AccountsResponse | null>(null);
  let value = $state("");
  let form: HTMLFormElement | undefined = $state();
  let picked = $state<{ external_id: string; display_name: string; config: string } | null>(null);

  async function load() {
    loading = true;
    try {
      const query = new URLSearchParams({ source });
      // Read only for the sources that need it, so switching the website select above does not
      // refetch the four pickers whose answer it cannot change: an unread prop is an untracked
      // one, which makes this branch the dependency list as well as the query string.
      if (needsWebsite) query.set("website_id", websiteId);
      const res = await fetch(`/marketing/accounts?${query.toString()}`);
      response = (await res.json()) as AccountsResponse;
    } catch {
      response = {
        source,
        connected: false,
        has_scope: false,
        configured: true,
        accounts: [],
        error: "marketing.accounts_error",
        connect_flag: "",
        connected_via: [],
      };
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // Nothing to ask yet. `loading` is cleared rather than left true, or the branch below would
    // render "loading available accounts…" forever over a request that was never made.
    if (missingWebsite) {
      response = null;
      loading = false;
      return;
    }
    void load();
  });

  const colleagues = $derived(
    (response?.connected_via ?? []).map((o) => `${o.name} (${o.email})`).join(", "),
  );
  const linked = $derived(new Set(linkedIds));
  const items = $derived(
    (response?.accounts ?? [])
      .filter((a) => !linked.has(a.external_id))
      .map((a) => ({
        value: a.external_id,
        label: a.display_name,
        hint: a.account_hint ?? undefined,
      })),
  );

  function choose(externalId: string) {
    const account = response?.accounts.find((a) => a.external_id === externalId);
    if (!account) return;
    picked = {
      external_id: account.external_id,
      display_name: account.display_name,
      config: JSON.stringify(account.config ?? {}),
    };
    // Wait for the hidden inputs to reflect `picked`, then submit to the host action.
    queueMicrotask(() => form?.requestSubmit());
  }
</script>

<div class="space-y-1.5">
  <span class="text-xs font-medium text-text-muted">{t(`marketing.picker.${source}`)}</span>
  {#if missingWebsite}
    <!-- Two different sentences, because they have two different cures: pick the site from the
         select above, or give this client a website first. One message covering both would be
         wrong for whichever half the reader is in. -->
    <p class="text-sm text-text-muted">
      {t(hasWebsites ? "marketing.rankmath_pick_website" : "marketing.rankmath_needs_website")}
    </p>
  {:else if loading}
    <p class="text-sm text-text-muted">{t("marketing.picker.loading")}</p>
  {:else if response && !response.connected}
    <p class="text-sm text-text-muted">
      {t("marketing.empty.needs_connection")}
    </p>
    {#if colleagues}
      <!-- A colleague's grant already reaches this source; picking accounts still needs the
           caller's own, but knowing whose it is beats a bare "not connected". -->
      <p class="text-xs text-text-muted">
        {t("marketing.connected_via_other", { who: colleagues })}
      </p>
    {/if}
    <a
      href={connect}
      data-sveltekit-preload-data="off"
      class="text-sm font-medium text-brand hover:underline"
    >
      {t("marketing.connect_cta")}
    </a>
  {:else if response && !response.has_scope}
    <p class="text-sm text-text-muted">
      {t("marketing.no_scope", { source: t(`marketing.source.${source}`) })}
    </p>
    {#if colleagues}
      <p class="text-xs text-text-muted">
        {t("marketing.connected_via_other", { who: colleagues })}
      </p>
    {/if}
    <a
      href={connect}
      data-sveltekit-preload-data="off"
      class="text-sm font-medium text-brand hover:underline"
    >
      {t("marketing.reconnect")}
    </a>
  {:else if response && !response.configured}
    <!-- "Not configured" means a different thing per source and has a different cure. Ads
         needs a developer token; SE Ranking needs the agency's API key. Neither is fixed by
         reconnecting Google, so neither offers that — and the message carries the link to
         where it *is* fixed, rather than naming a screen and leaving you to find it. -->
    {#if needsWebsite}
      <!-- A site-key credential is not agency configuration, so Instellingen cures nothing here:
           the WordPress connection lives on this one website's page. The API collapses "no
           credential" and "credential refused" into the same `configured=false`, and with a site
           named the first is overwhelmingly the case — so name it, and link to the panel that
           fixes it. -->
      <p class="text-sm text-text-muted">{t("marketing.rankmath_not_connected")}</p>
      <a href="/websites/{websiteId}" class="text-sm font-medium text-brand hover:underline">
        {t("marketing.picker.connect_wordpress")}
      </a>
    {:else}
      <p class="text-sm text-text-muted">
        {t(
          source === "gads" ? "marketing.ads_not_configured" : `marketing.${source}_not_configured`,
        )}
      </p>
      <a href="/settings/marketing" class="text-sm font-medium text-brand hover:underline">
        {t("marketing.picker.configure")}
      </a>
    {/if}
  {:else if response?.error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(response.error)}</p>
    <!-- A disabled Cloud API is not a token problem: a reconnect mints the same token against
         the same project and fails identically, so don't offer it as the cure. Neither is a
         sunset Google Ads API version — that one is fixed by upgrading, not by consenting. -->
    {#if response?.error !== "marketing.api_not_enabled" && response?.error !== "marketing.ads_api_version"}
      <a
        href={connect}
        data-sveltekit-preload-data="off"
        class="text-sm font-medium text-brand hover:underline"
      >
        {t("marketing.reconnect")}
      </a>
    {/if}
  {:else if items.length === 0}
    <p class="text-sm text-text-muted">{t("marketing.picker.none")}</p>
  {:else}
    <Combobox
      {items}
      name="_marketing_pick_{source}"
      id="marketing-pick-{source}"
      bind:value
      allowEmpty={false}
      placeholder={t("marketing.picker.placeholder")}
      onselect={choose}
    />
  {/if}

  <!-- Submitted programmatically on select; posts to the company page's ?/marketingLink action. -->
  <form
    bind:this={form}
    method="POST"
    action="?/marketingLink"
    use:enhance={() =>
      ({ update }) => {
        value = "";
        picked = null;
        return update();
      }}
    class="hidden"
  >
    <input type="hidden" name="source" value={source} />
    <input type="hidden" name="website_id" value={websiteId} />
    <input type="hidden" name="external_id" value={picked?.external_id ?? ""} />
    <input type="hidden" name="display_name" value={picked?.display_name ?? ""} />
    <input type="hidden" name="config" value={picked?.config ?? "{}"} />
  </form>
</div>
