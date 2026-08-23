<script lang="ts">
  /**
   * One dialog for "connect a marketing source" (#338), wherever the question is asked.
   *
   * Before it there were two ways to attach a client's Google Ads account and they produced
   * different states: the marketing panel's picker wrote both the marketing link and the
   * `google_ads` account row, while Instellingen → Google Ads — the one the Google Ads panel
   * actually pointed at — wrote only the account, so the client's marketing panel and
   * `/marketing` went on saying nothing was connected. The API now mirrors either direction, and
   * this is the front door: **one control, posting to `POST /marketing/links`**, reachable from
   * the client's page, from `/marketing/google-ads` and from `/marketing`.
   *
   * It composes rather than re-implements: `MarketingSourcePickers` is the whole connect
   * surface — the website select, the per-source pickers and the contributed connections — and
   * this dialog adds the one half the client's own page never has to ask: *which client*, as the
   * house picker with inline-create (docs/UX.md), shown only when the host cannot answer it from
   * the route.
   *
   * It used to mount the pickers itself with `hasWebsites={false}` written in (#399), which made
   * the Rank Math row read *"deze klant heeft nog geen website"* for every client — including
   * ones with two — with no site select on the dialog to correct it with. A dialog that asks
   * which client has to be able to ask which website, so it fetches this client's sites on the
   * same lazy terms it fetches the client list.
   */
  import { page } from "$app/state";

  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import {
    companyArchivedLabel,
    splitCompanyOptions,
    type PickerCompany,
  } from "$lib/modules/companies/picker";

  import MarketingSourcePickers from "./MarketingSourcePickers.svelte";
  import { connectHref, SITE_KEY_SOURCES, type MarketingSource } from "./types";

  let {
    open = $bindable(false),
    companyId = "",
    companies = null,
    websites = null,
    companyDefinitions = null,
    locale = "",
    sources,
    linkedIds = {},
    action = "?/marketingLink",
    gtmAction = "?/gtmLink",
    connectors = true,
    title,
    error = null,
    qcError = null,
    inlineCreated = null,
  }: {
    open?: boolean;
    /** The client, when the host already knows it. Empty means "ask", below. */
    companyId?: string;
    /**
     * Options for the client picker; ignored when `companyId` is set.
     *
     * `null` (the default) means the host has none to give — not "there are none" — so the
     * dialog fetches its own on first open, the pattern `CompanyQuickCreate` already uses for
     * its definitions. Without it, `/marketing` would pay a client-list read on every render to
     * fill a picker most visits never open (docs/PERFORMANCE.md). A host that loads the names
     * anyway (`/marketing/google-ads` prints them under each card) passes them and pays nothing.
     */
    companies?: PickerCompany[] | null;
    /**
     * This client's websites, when the host already knows them (the company panel does).
     *
     * `null` means "ask for them when a client is chosen", not "there are none" — the same
     * distinction `companies` above makes, for the same reason: `/marketing` must not pay a
     * websites read on every render to fill a select most visits never see.
     */
    websites?: { id: string; name: string }[] | null;
    /** The host's already-loaded company custom-field definitions, or null to let it fetch. */
    companyDefinitions?: CustomFieldDefinition[] | null;
    /** Only reached through the client picker's ＋, so a host that fixes the client needs none. */
    locale?: string;
    /** Which sources to offer. `/marketing/google-ads` passes one; `/marketing` passes them all. */
    sources: MarketingSource[];
    /** Per source, the external ids already linked to this client — filtered out of the options. */
    linkedIds?: Partial<Record<MarketingSource, string[]>>;
    action?: string;
    /** The host page's action for a contributed connection (#411) — `gtmActions` mounts it. */
    gtmAction?: string;
    /** A host narrowed to one source (`/marketing/google-ads`) offers no connections. */
    connectors?: boolean;
    title: string;
    /** The host page's `form?.error`, so a refused link is read where it was asked for. */
    error?: string | null;
    /** The host page's `form?.qcError` — a failed quick-create must not paint this dialog red. */
    qcError?: string | null;
    /** The host page's `form?.inlineCreated`, which auto-selects a just-created client. */
    inlineCreated?: { slot: string; id: string } | null;
  } = $props();

  const PICKER_SLOT = "marketing-connect-company";

  let chosen = $state("");
  let quickCreateOpen = $state(false);
  let quickCreateName = $state("");

  // The client is whichever the host fixed, else whichever was picked here.
  const company = $derived(companyId || chosen);
  const askForCompany = $derived(!companyId);

  // A client created through the ＋ selects itself, which is the whole point of inline-create:
  // the slot is echoed back so a sibling picker on the same page never steals the selection.
  $effect(() => {
    if (inlineCreated?.slot === PICKER_SLOT && inlineCreated.id) chosen = inlineCreated.id;
  });

  // Fetched on first open, never on page load. `requested` is a plain variable, not `$state`:
  // it guards the effect and reading it as state would make the write below re-run the effect.
  let fetched = $state<PickerCompany[] | null>(null);
  let capped = $state(false);
  let requested = false;
  $effect(() => {
    if (!open || !askForCompany || companies !== null || requested) return;
    requested = true;
    void (async () => {
      const response = await fetch("/marketing/companies", {
        headers: { accept: "application/json" },
      });
      const body = response.ok ? await response.json() : null;
      fetched = (body?.items ?? []) as PickerCompany[];
      capped = Boolean(body?.capped);
    })();
  });

  // The chosen client's websites, on the same lazy terms and for the same reason: a site-key
  // source (Rank Math) cannot be asked about at all until a site is named, and away from a
  // client's page nothing on the screen could answer it. Keyed by client, because the picker
  // above can change it — and a cache, because reopening the dialog on the same client must not
  // spend a round trip to learn what it already knows.
  const needsWebsites = $derived(sources.some((s) => SITE_KEY_SOURCES.includes(s)));
  let siteCache = $state<Record<string, { id: string; name: string }[]>>({});
  // A plain array, not `$state` and not a `SvelteSet`: it guards the effect below, and a
  // reactive one would make the effect's own write re-run it (the `requested` flag above,
  // one question over).
  const asked: string[] = [];
  $effect(() => {
    const id = company;
    if (!open || !id || !needsWebsites || websites !== null || asked.includes(id)) return;
    asked.push(id);
    void (async () => {
      const response = await fetch(`/marketing/websites?company=${encodeURIComponent(id)}`, {
        headers: { accept: "application/json" },
      });
      const body = response.ok ? await response.json() : null;
      // Written as a whole object rather than mutated: `siteCache[id] = …` on a `$state` proxy
      // works, but reading `siteCache` back below is what makes this effect its own dependency.
      siteCache = { ...siteCache, [id]: (body?.items ?? []) as { id: string; name: string }[] };
    })();
  });
  // `[]` is the honest answer while the read is in flight *and* when the client really has no
  // website: the picker's two "no site" sentences differ, and the wrong one for a second is a
  // better screen than a spinner in a select nobody was looking at.
  const siteOptions = $derived(websites ?? siteCache[company] ?? []);

  const options = $derived(companies ?? fetched ?? []);
  // An archived client is not somebody you are wiring up an ad account for, so it drops behind
  // the search — and stays findable there, because "not suggested" is not "does not exist".
  const companyPicker = $derived(splitCompanyOptions(options, { selectedId: chosen }));
  const companyItems = $derived(companyPicker.live);

  // Consent comes back to the page the user was on, which is where they will look for the
  // dialog again. `data-sveltekit-preload-data="off"` on the link: this is an API redirect out
  // to Google, not a route to prefetch.
  const connect = $derived(connectHref(page.url.pathname + page.url.search));

  function quickCreate(query: string) {
    quickCreateName = query;
    quickCreateOpen = true;
  }
</script>

<Modal bind:open {title}>
  <div class="space-y-4">
    {#if error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}

    {#if askForCompany}
      <div>
        <label for="marketing-connect-company" class="mb-1 block text-sm font-medium text-text">
          {t("marketing.connect.client")}
        </label>
        <Combobox
          items={companyItems}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="_marketing_connect_company"
          id="marketing-connect-company"
          bind:value={chosen}
          placeholder={t("marketing.connect.client_placeholder")}
          oncreate={quickCreate}
        />
        {#if capped}
          <!-- The list is a prefix. Saying so beats letting the client who is missing read as a
               client who cannot be connected (CLAUDE.md §9) — and the client page still has the
               same control for anyone past the cap. -->
          <p class="mt-1 text-xs text-text-muted">{t("marketing.connect.client_capped")}</p>
        {/if}
      </div>
    {/if}

    {#if company}
      <div class="space-y-4 border-t border-border pt-4">
        <MarketingSourcePickers
          companyId={company}
          websites={siteOptions}
          {sources}
          {linkedIds}
          {action}
          {gtmAction}
          {connectors}
        />
        <p class="text-xs text-text-muted">{t("marketing.connect.hint")}</p>
      </div>
    {:else}
      <!-- Nothing to ask Google yet, and saying so beats an empty box: the picker below would
           otherwise render "no accounts" over a question that was never put. -->
      <p class="border-t border-border pt-4 text-sm text-text-muted">
        {t("marketing.connect.pick_client_first")}
      </p>
      <a
        href={connect}
        data-sveltekit-preload-data="off"
        class="text-sm font-medium text-brand hover:underline"
      >
        {t("marketing.connect_cta")}
      </a>
    {/if}
  </div>
</Modal>

{#if askForCompany}
  <!-- Mounted only where the client picker is: with the client fixed by the route there is no
       ＋ to press, and a dialog nobody can open must not cost the definitions fetch behind it. -->
  <CompanyQuickCreate
    bind:open={quickCreateOpen}
    name={quickCreateName}
    pickerSlot={PICKER_SLOT}
    definitions={companyDefinitions}
    {locale}
    error={qcError}
  />
{/if}
