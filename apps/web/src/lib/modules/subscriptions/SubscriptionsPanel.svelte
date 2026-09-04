<script lang="ts">
  /** A client's recurring agreements, on the company detail page (issue #30). */
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import { getLocale } from "$lib/paraglide/runtime";

  import SubscriptionDialog from "./SubscriptionDialog.svelte";

  interface PanelSubscription {
    id: string;
    name: string;
    status: string;
    amount: string | null;
    currency: string;
    interval: string;
    next_invoice_date: string | null;
  }

  let {
    companyId,
    data,
    locale = getLocale(),
  }: { companyId: string; data: Record<string, unknown>; locale?: string } = $props();
  const subscriptions = $derived((data.subscriptions ?? []) as PanelSubscription[]);
  // Capped and counted since #407: this read had no limit at all, so the card's length was the
  // client's — the agency's biggest client is exactly the page that became unreadable.
  const total = $derived((data.total as number | undefined) ?? subscriptions.length);
  const forbidden = $derived(Boolean(data.forbidden));
  const canWrite = $derived(can(page.data.user, "subscriptions.subscription.write"));
  /** The list narrowed to this client — the hand-over for the rows the card does not show. */
  const listHref = $derived(`/subscriptions?company=${companyId}`);

  // Record an agreement from where the client is, and *stay* there: the module's own form in
  // a dialog (`SubscriptionDialog`), hosted by this panel exactly as the Uren panel hosts its
  // log-hours dialog (#402). It used to be a link to `/subscriptions?company=…&new=1` — the
  // client was carried through and the way back was not.
  let adding = $state(false);

  function money(row: PanelSubscription): string | null {
    if (row.amount == null) return null;
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: row.currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(row.amount));
  }

  function meta(row: PanelSubscription): string {
    const interval = t(`subscriptions.interval.${row.interval}`);
    return row.next_invoice_date
      ? `${interval} · ${t("subscriptions.field.next_invoice")} ${fmtNumericDate(row.next_invoice_date)}`
      : interval;
  }
</script>

{#if forbidden}
  <!-- Money is permission-gated; the panel stays quiet rather than erroring the page. -->
{:else}
  <PanelRows
    rows={subscriptions}
    {total}
    href={listHref}
    linkLabel={t("subscriptions.panel.view_all", { count: total })}
  >
    {#snippet children(shown)}
      {#if shown.length === 0}
        <p class="text-sm text-text-muted">{t("subscriptions.panel.empty")}</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each shown as sub (sub.id)}
            <PanelRow
              href={fromHref(`/subscriptions/${sub.id}`, page.url)}
              title={sub.name}
              meta={meta(sub)}
              value={money(sub)}
              chip={t(`subscriptions.status.${sub.status}`)}
            />
          {/each}
        </ul>
      {/if}
    {/snippet}
    {#snippet actions()}
      {#if canWrite}
        <button type="button" onclick={() => (adding = true)} class="text-brand hover:underline">
          ＋ {t("subscriptions.add")}
        </button>
      {/if}
    {/snippet}
  </PanelRows>

  {#if canWrite}
    <SubscriptionDialog bind:open={adding} {companyId} {locale} />
  {/if}
{/if}
