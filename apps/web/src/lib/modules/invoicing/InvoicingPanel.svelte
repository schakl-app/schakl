<script lang="ts">
  /** Invoicing on the company page (issue #207): recent invoices with their open balance
   * (overdue loudly red — UX Principle 4) and recent quotes. Rendered from the API panel's
   * data; every number links to the document behind it (Principle 7). */
  import { t } from "$lib/core/i18n";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelInvoice {
    id: string;
    number: string | null;
    kind: string;
    status: string;
    issue_date: string | null;
    due_date: string | null;
    overdue: boolean;
    total: string;
    outstanding: string;
    currency: string;
  }
  interface PanelQuote {
    id: string;
    number: string | null;
    status: string;
    valid_until: string | null;
    total: string;
    currency: string;
  }

  const invoices = $derived((data.invoices ?? []) as PanelInvoice[]);
  const quotes = $derived((data.quotes ?? []) as PanelQuote[]);
  const money = (value: string, currency: string) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(value));
  const dmy = (iso: string | null) => (iso ? iso.split("-").reverse().join("-") : "—");
</script>

{#if data.forbidden}
  <p class="text-sm text-text-muted">—</p>
{:else if invoices.length === 0 && quotes.length === 0}
  <p class="text-sm text-text-muted">{t("invoicing.panel.empty")}</p>
{:else}
  {#if invoices.length > 0}
    <ul class="divide-y divide-border">
      {#each invoices as invoice (invoice.id)}
        <li class="flex items-center justify-between gap-3 py-2 text-sm">
          <div class="min-w-0">
            <a href="/invoices/{invoice.id}" class="font-medium text-text hover:text-brand">
              {invoice.number ?? t(`invoicing.status.${invoice.status}`)}
            </a>
            <span class="ml-2 text-xs text-text-muted">{dmy(invoice.issue_date)}</span>
            {#if invoice.overdue}
              <span
                class="ml-2 rounded-md bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300"
                >{t("invoicing.status.overdue")}</span
              >
            {:else}
              <span class="ml-2 rounded-md bg-surface px-1.5 py-0.5 text-xs text-text-muted"
                >{t(`invoicing.status.${invoice.status}`)}</span
              >
            {/if}
          </div>
          <div class="shrink-0 text-right tabular-nums">
            <span class="text-text">{money(invoice.total, invoice.currency)}</span>
            {#if invoice.status === "open" && Number(invoice.outstanding) !== Number(invoice.total)}
              <span class="block text-xs text-text-muted">
                {money(invoice.outstanding, invoice.currency)}
                {t("invoicing.panel.outstanding")}
              </span>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
  {#if quotes.length > 0}
    <p class="mt-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("invoicing.panel.quotes")}
    </p>
    <ul class="divide-y divide-border">
      {#each quotes as quote (quote.id)}
        <li class="flex items-center justify-between gap-3 py-2 text-sm">
          <div class="min-w-0">
            <a href="/quotes/{quote.id}" class="font-medium text-text hover:text-brand">
              {quote.number ?? t(`invoicing.quote_status.${quote.status}`)}
            </a>
            <span class="ml-2 rounded-md bg-surface px-1.5 py-0.5 text-xs text-text-muted"
              >{t(`invoicing.quote_status.${quote.status}`)}</span
            >
          </div>
          <span class="shrink-0 tabular-nums text-text">{money(quote.total, quote.currency)}</span>
        </li>
      {/each}
    </ul>
  {/if}
{/if}
