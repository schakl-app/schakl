<script lang="ts">
  /** A client's recurring agreements, on the company detail page (issue #30). */
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

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
{:else if subscriptions.length === 0}
  <p class="text-sm text-text-muted">{t("subscriptions.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each subscriptions as sub (sub.id)}
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
{#if !forbidden && can(page.data.user, "subscriptions.subscription.write")}
  <!-- Quick-create from the client page: opens the agreement dialog with this client set. -->
  <a
    href={`/subscriptions?company=${companyId}&new=1`}
    class="mt-3 inline-block text-xs text-brand hover:underline"
  >
    ＋ {t("subscriptions.add")}
  </a>
{/if}
