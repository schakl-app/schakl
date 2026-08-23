<script lang="ts">
  /** A client's recurring agreements, on the company detail page (issue #30). */
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  interface PanelSubscription {
    id: string;
    name: string;
    status: string;
    amount: string | null;
    currency: string;
    interval: string;
    next_invoice_date: string | null;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const subscriptions = $derived((data.subscriptions ?? []) as PanelSubscription[]);
  // Capped and counted since #407: this read had no limit at all, so the card's length was the
  // client's — the agency's biggest client is exactly the page that became unreadable.
  const total = $derived((data.total as number | undefined) ?? subscriptions.length);
  const forbidden = $derived(Boolean(data.forbidden));

  function money(row: PanelSubscription): string {
    if (row.amount == null) return "—";
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: row.currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(row.amount));
  }
</script>

{#if forbidden}
  <!-- Money is permission-gated; the panel stays quiet rather than erroring the page. -->
{:else}
  <PanelRows
    rows={subscriptions}
    {total}
    href={`/subscriptions?company=${companyId}`}
    linkLabel={t("subscriptions.panel.view_all", { count: total })}
  >
    {#snippet children(shown)}
      {#if shown.length === 0}
        <p class="text-sm text-text-muted">{t("subscriptions.panel.empty")}</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each shown as sub (sub.id)}
            <li class="flex flex-wrap items-center gap-2 py-2">
              <a
                href="/subscriptions"
                class="min-w-0 flex-1 truncate text-sm font-medium text-brand hover:underline"
                >{sub.name}</a
              >
              <span class="text-sm tabular-nums text-text"
                >{money(sub)} · {t(`subscriptions.interval.${sub.interval}`)}</span
              >
              {#if sub.next_invoice_date}
                <span class="text-xs text-text-muted">
                  {t("subscriptions.field.next_invoice")}: {fmtNumericDate(sub.next_invoice_date)}
                </span>
              {/if}
              <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                >{t(`subscriptions.status.${sub.status}`)}</span
              >
            </li>
          {/each}
        </ul>
      {/if}
    {/snippet}
    {#snippet actions()}
      {#if can(page.data.user, "subscriptions.subscription.write")}
        <!-- Quick-create from the client page: opens the dialog with this client set. -->
        <a href={`/subscriptions?company=${companyId}&new=1`} class="text-brand hover:underline">
          ＋ {t("subscriptions.add")}
        </a>
      {/if}
    {/snippet}
  </PanelRows>
{/if}
