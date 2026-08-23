<script lang="ts">
  /** Invoicing on the company page (issue #207): recent invoices with their open balance
   * (overdue loudly red — UX Principle 4) and recent quotes. Rendered from the API panel's
   * data; every number links to the document behind it (Principle 7). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

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
  // Both lists were capped in silence *and* had no footer link at all (#407), so a client with
  // sixty invoices and one with six drew the same card and neither offered a way to the ledger.
  const invoiceTotal = $derived((data.invoice_total as number | undefined) ?? invoices.length);
  const quoteTotal = $derived((data.quote_total as number | undefined) ?? quotes.length);
  //: This card is the only one on the hub that draws *two* lists, so eight invoices over five
  //: quotes made it thirteen rows — the longest block on the page and the one the team's
  //: complaint is actually about. Each half opens on a working handful and expands in place.
  const INVOICES_SHOWN = 5;
  const QUOTES_SHOWN = 3;
  const money = (value: string, currency: string) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(value));
  const dmy = (iso: string | null) => (iso ? iso.split("-").reverse().join("-") : "—");
</script>

{#snippet addInvoice()}
  {#if can(page.data.user, "invoicing.invoice.write")}
    <!-- New invoice from where the client is (owner feedback): opens the invoice form with
         this client preset. -->
    <a href={`/invoices/new?company=${companyId}`} class="text-brand hover:underline">
      ＋ {t("invoicing.new_invoice")}
    </a>
  {/if}
{/snippet}

{#if data.forbidden}
  <p class="text-sm text-text-muted">—</p>
{:else if invoices.length === 0 && quotes.length === 0}
  <p class="text-sm text-text-muted">{t("invoicing.panel.empty")}</p>
  <p class="mt-3 text-xs">{@render addInvoice()}</p>
{:else}
  {#if invoices.length > 0}
    <PanelRows
      rows={invoices}
      collapsed={INVOICES_SHOWN}
      total={invoiceTotal}
      href={`/invoices?company=${companyId}`}
      linkLabel={t("invoicing.panel.view_all", { count: invoiceTotal })}
      actions={quotes.length > 0 ? undefined : addInvoice}
    >
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as invoice (invoice.id)}
            <li class="flex items-center justify-between gap-3 py-2 text-sm">
              <div class="min-w-0">
                <a href={fromHref(`/invoices/${invoice.id}`, page.url)} class="font-medium text-text hover:text-brand">
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
      {/snippet}
    </PanelRows>
  {/if}
  {#if quotes.length > 0}
    <p class="mt-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("invoicing.panel.quotes")}
    </p>
    <PanelRows
      rows={quotes}
      collapsed={QUOTES_SHOWN}
      total={quoteTotal}
      href={`/quotes?company=${companyId}`}
      linkLabel={t("invoicing.panel.view_all_quotes", { count: quoteTotal })}
      actions={addInvoice}
    >
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as quote (quote.id)}
            <li class="flex items-center justify-between gap-3 py-2 text-sm">
              <div class="min-w-0">
                <a href={fromHref(`/quotes/${quote.id}`, page.url)} class="font-medium text-text hover:text-brand">
                  {quote.number ?? t(`invoicing.quote_status.${quote.status}`)}
                </a>
                <span class="ml-2 rounded-md bg-surface px-1.5 py-0.5 text-xs text-text-muted"
                  >{t(`invoicing.quote_status.${quote.status}`)}</span
                >
              </div>
              <span class="shrink-0 tabular-nums text-text"
                >{money(quote.total, quote.currency)}</span
              >
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
{/if}
