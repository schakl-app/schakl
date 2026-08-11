<script lang="ts">
  /**
   * The Google Ads accounts linked to a client, on the company detail page.
   *
   * Renders from stored rows only — the panel never waits for Google. A company page composes
   * every module's panel in sequence, so an integration that took three seconds here would take
   * three seconds off every client page load. The numbers live one click away, where waiting is
   * the point rather than a surprise.
   */
  import { AlertTriangle, Megaphone } from "@lucide/svelte";

  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  interface PanelAccount {
    id: string;
    customer_id: string;
    name: string;
    currency?: string | null;
    status: string;
    last_error?: string | null;
    last_verified_at?: string | null;
  }

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();
  const forbidden = $derived(Boolean(data.forbidden));
  const accounts = $derived((data.accounts ?? []) as PanelAccount[]);
  const canManage = $derived(Boolean(data.can_manage));
</script>

{#if forbidden}
  <!-- Permission-gated: stay quiet rather than error the page. A card reading "no access" on a
       page full of working cards teaches nobody anything. -->
{:else if accounts.length === 0}
  <p class="text-sm text-text-muted">{t("google_ads.panel.empty")}</p>
  {#if canManage}
    <a href="/settings/google-ads" class="mt-2 inline-block text-sm text-brand hover:underline">
      {t("google_ads.panel.link_account")}
    </a>
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
              {fmtNumericDate(account.last_verified_at)}
            {/if}
          </span>
          {#if account.status === "error"}
            <!-- Google's own sentence, verbatim. It is the one thing that says *what* to fix,
                 and the glyph carries the state rather than the colour alone: the brand colour
                 is gold on some tenants and would read as a warning everywhere. -->
            <span class="mt-1 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words">{account.last_error ?? t("google_ads.panel.error")}</span>
            </span>
          {/if}
        </div>
      </li>
    {/each}
  </ul>
{/if}
