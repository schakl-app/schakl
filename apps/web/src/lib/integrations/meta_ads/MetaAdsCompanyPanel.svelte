<script lang="ts">
  /**
   * Meta Ads, on a client's page: the ad accounts managed for them, each a link to its
   * campaigns. Everything drawn came down with the page (docs/PERFORMANCE.md); the figures
   * are a live read and live on the account's own page.
   */
  import { t } from "$lib/core/i18n";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";

  interface PanelAccount {
    id: string;
    name: string;
    meta_id: string;
    currency: string | null;
    status: string;
  }

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  const items = $derived((data.items ?? []) as PanelAccount[]);
</script>

<ul class="divide-y divide-border">
  {#each items as account (account.id)}
    <PanelRow
      href={`/marketing/meta-ads/${account.id}`}
      title={account.name}
      meta={[account.meta_id, account.currency].filter(Boolean).join(" · ")}
      chip={account.status === "error" ? t("meta.channels.state_error") : null}
      chipState="late"
    />
  {/each}
</ul>
