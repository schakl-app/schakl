<script lang="ts">
  /** The linked Meta ad accounts, one card each: whose it is, and whether it answers. */
  import Megaphone from "@lucide/svelte/icons/megaphone";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import { errorText } from "$lib/integrations/meta/format";
  import { accountStatusLabel } from "$lib/integrations/meta_ads/format";

  let { data } = $props();
  const title = $derived(navLabel("meta_ads", t("nav.meta_ads")));
</script>

<svelte:head>
  <title>{pageTitle(title)}</title>
</svelte:head>

<PageHeader {title}>
  {#snippet subtitle()}{t("meta_ads.page.subtitle")}{/snippet}
  {#snippet actions()}
    {#if data.canManage}
      <a
        href="/marketing/social/channels?kind=ad_account"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text hover:border-text-muted"
      >
        {t("meta_ads.page.link")}
      </a>
    {/if}
  {/snippet}
</PageHeader>

{#if data.loadError}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(data.loadError)}</p>
{/if}

{#if data.accounts.length === 0}
  <div class="rounded-xl border border-dashed border-border px-6 py-12 text-center">
    <Megaphone size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
    <p class="text-sm text-text">{t("meta_ads.page.empty")}</p>
    <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">
      {data.canManage ? t("meta_ads.page.empty_hint") : t("meta.setup.ask_admin")}
    </p>
    {#if data.canManage}
      <a
        href="/marketing/social/channels?kind=ad_account&state=unlinked"
        class="mt-4 inline-flex rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("meta_ads.page.link")}
      </a>
    {/if}
  </div>
{:else}
  <ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
    {#each data.accounts as account (account.id)}
      <li>
        <a
          href="/marketing/meta-ads/{account.id}"
          class="block h-full rounded-xl border border-border bg-surface-raised p-4 hover:border-brand"
          data-sveltekit-preload-data="hover"
        >
          <div class="flex items-start gap-2">
            <Megaphone size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium text-text">{account.name}</span>
              <span class="mt-0.5 block truncate text-xs text-text-muted">
                {[account.meta_id, account.currency].filter(Boolean).join(" · ")}
              </span>
              <span class="mt-1 block truncate text-xs text-text-muted">
                {account.company_name ?? t("meta_ads.own_account")}
              </span>
            </div>
          </div>
          {#if account.status === "error" || accountStatusLabel(account.account_status) || !account.can_read}
            <!-- The glyph carries the state, not the colour. -->
            <span class="mt-3 flex items-start gap-1.5 text-xs text-text">
              <TriangleAlert size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words">
                {#if account.status === "error"}
                  {errorText(account.last_error) || t("meta.channels.state_error")}
                {:else if !account.can_read}
                  {t("meta_ads.page.no_scope")}
                {:else}
                  {accountStatusLabel(account.account_status)}
                {/if}
              </span>
            </span>
          {:else if !account.can_write}
            <span class="mt-3 block text-xs text-text-muted">{t("meta_ads.page.read_only")}</span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>
{/if}
