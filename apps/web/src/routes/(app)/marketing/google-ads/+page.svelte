<script lang="ts">
  /**
   * The linked Google Ads accounts. A directory, not a dashboard: pick an account and the
   * numbers are on its own page, where the wait for Google is expected.
   */
  import { AlertTriangle, Megaphone, Plus } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import MarketingConnectDialog from "$lib/modules/marketing/MarketingConnectDialog.svelte";

  let { data, form } = $props();

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );

  // Opened from the header and from the empty state — the two places somebody stands when they
  // notice an account is missing. Both used to point at Instellingen → Google Ads.
  let connecting = $state(false);
</script>

<svelte:head>
  <title>{pageTitle(navLabel("google_ads", t("nav.google_ads")))}</title>
</svelte:head>

<div class="mb-4 flex items-start justify-between gap-4">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("google_ads", t("nav.google_ads"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("google_ads.page.subtitle")}</p>
  </div>
  <div class="flex shrink-0 items-center gap-2">
    {#if data.canLink}
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => (connecting = true)}
      >
        <Plus size={15} aria-hidden="true" />
        {t("google_ads.page.link_first")}
      </button>
    {/if}
    {#if data.canManage}
      <!-- Instellingen keeps the developer token, the house policy and the agency's *own*
           account — the one thing that has no client and therefore cannot be a marketing link.
           It is no longer where you go to connect a client's. -->
      <a
        href="/settings/google-ads"
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
      >
        {t("google_ads.page.manage")}
      </a>
    {/if}
  </div>
</div>

{#if data.accounts.length === 0}
  <div class="rounded-xl border border-border bg-surface-raised p-8 text-center">
    <Megaphone size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
    <p class="text-sm text-text-muted">{t("google_ads.page.empty")}</p>
    {#if data.canLink}
      <button
        type="button"
        class="mt-3 inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => (connecting = true)}
      >
        <Plus size={15} aria-hidden="true" />
        {t("google_ads.page.link_first")}
      </button>
    {/if}
  </div>
{:else}
  <ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
    {#each data.accounts as account (account.id)}
      <li>
        <a
          href="/marketing/google-ads/{account.id}"
          class="block h-full rounded-xl border border-border bg-surface-raised p-4 hover:border-brand"
        >
          <div class="flex items-start gap-2">
            <Megaphone size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium text-text"
                >{account.descriptive_name}</span
              >
              <span class="mt-0.5 block truncate text-xs text-text-muted">
                {account.customer_id_formatted}
                {#if account.currency_code}· {account.currency_code}{/if}
              </span>
              {#if account.company_id}
                <span class="mt-1 block truncate text-xs text-text-muted">
                  {companyName(account.company_id)}
                </span>
              {/if}
            </div>
          </div>
          {#if account.status === "error"}
            <!-- The glyph carries the state, not the colour: `text-brand` is gold on some
                 tenants and would read as a warning on every card. -->
            <span class="mt-3 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words">
                {account.last_error ?? t("google_ads.panel.error")}
              </span>
            </span>
          {:else if account.last_verified_at}
            <span class="mt-3 block text-xs text-text-muted">
              {t("google_ads.panel.verified")}
              {fmtDateTime(account.last_verified_at)}
            </span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>
{/if}

{#if data.canLink}
  <MarketingConnectDialog
    bind:open={connecting}
    companies={data.companies}
    locale={data.locale}
    sources={["gads"]}
    title={t("google_ads.page.link_first")}
    error={form?.error ?? null}
    qcError={form?.qcError ?? null}
    inlineCreated={form?.inlineCreated ?? null}
  />
{/if}
