<script lang="ts">
  /**
   * The Google Ads accounts linked to a client, on the company detail page.
   *
   * Renders from stored rows only — the panel never waits for Google. A company page composes
   * every module's panel in sequence, so an integration that took three seconds here would take
   * three seconds off every client page load. The numbers live one click away, where waiting is
   * the point rather than a surprise.
   */
  import { AlertTriangle, Megaphone, Plus } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  // Cross-module by design (#338): connecting a client's Ads account writes a *marketing link*,
  // which is what makes the panel above this one, `/marketing` and this panel agree afterwards.
  // Owning a second control here would be owning a second outcome — the bug this fixes.
  import MarketingConnectDialog from "$lib/modules/marketing/MarketingConnectDialog.svelte";

  interface PanelAccount {
    id: string;
    customer_id: string;
    name: string;
    currency?: string | null;
    status: string;
    last_error?: string | null;
    last_verified_at?: string | null;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const forbidden = $derived(Boolean(data.forbidden));
  const accounts = $derived((data.accounts ?? []) as PanelAccount[]);
  // Connecting posts a *marketing* link, so the control mirrors that key rather than this
  // module's (#310): gating it on `can_manage` would draw a button the API then refuses.
  const canLink = $derived(Boolean(data.can_link));

  let connecting = $state(false);
  // The picker filters what this client already has, so the obvious mistake — linking the same
  // account twice — is not on offer. Unformatted: the account list stores the bare customer id.
  const linked = $derived(accounts.map((a) => a.customer_id.replace(/\D/g, "")));
</script>

{#if forbidden}
  <!-- Permission-gated: stay quiet rather than error the page. A card reading "no access" on a
       page full of working cards teaches nobody anything. -->
{:else if accounts.length === 0}
  <p class="text-sm text-text-muted">{t("google_ads.panel.empty")}</p>
  {#if canLink}
    <!-- It used to be a link to Instellingen → Google Ads: an org-wide screen that dropped the
         client you were looking at and asked you to hand-type the customer id and the manager id
         Google can be asked for. Every other panel on this page keeps the client (`＋ Nieuwe
         website`, `＋ Nieuw domein`); this one now does too. -->
    <button
      type="button"
      class="mt-2 inline-flex items-center gap-1 text-sm text-brand hover:underline"
      onclick={() => (connecting = true)}
    >
      <Plus size={14} aria-hidden="true" />
      {t("google_ads.panel.link_account")}
    </button>
  {/if}
{:else}
  <ul class="divide-y divide-border">
    {#each accounts as account (account.id)}
      <li class="flex items-start gap-2 py-2">
        <Megaphone size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <a
            href="/marketing/google-ads/{account.id}"
            class="block truncate text-sm font-medium text-brand hover:underline"
          >
            {account.name}
          </a>
          <span class="mt-0.5 block truncate text-xs text-text-muted">
            {account.customer_id}
            {#if account.currency}· {account.currency}{/if}
            {#if account.last_verified_at}
              · {t("google_ads.panel.verified")}
              {fmtDateTime(account.last_verified_at)}
            {/if}
          </span>
          {#if account.status === "error"}
            <!-- Google's own sentence, verbatim. It is the one thing that says *what* to fix,
                 and the glyph carries the state rather than the colour alone: the brand colour
                 is gold on some tenants and would read as a warning everywhere. -->
            <span class="mt-1 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words"
                >{account.last_error ?? t("google_ads.panel.error")}</span
              >
            </span>
          {/if}
        </div>
      </li>
    {/each}
  </ul>
  {#if canLink}
    <!-- A client with one Ads account often gets a second (a separate brand, a second market),
         so the control stays after the first one — the same shape as every other panel's ＋. -->
    <button
      type="button"
      class="mt-2 inline-flex items-center gap-1 text-sm text-brand hover:underline"
      onclick={() => (connecting = true)}
    >
      <Plus size={14} aria-hidden="true" />
      {t("google_ads.panel.link_another")}
    </button>
  {/if}
{/if}

{#if canLink}
  <!-- The client is the route here, so no client picker and no ＋-new-client behind it. Posts to
       the company page's own `?/marketingLink`, which `marketingActions` already mounts. -->
  <MarketingConnectDialog
    bind:open={connecting}
    {companyId}
    sources={["gads"]}
    linkedIds={{ gads: linked }}
    title={t("google_ads.panel.connect_title")}
  />
{/if}
