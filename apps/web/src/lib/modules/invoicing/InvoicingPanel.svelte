<script lang="ts">
  /** Invoicing on the company page (issue #207): recent invoices with their open balance
   * (overdue loudly red — UX Principle 4) and recent quotes. Rendered from the API panel's
   * data; every number links to the document behind it (Principle 7). */
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelInvoice {
    id: string;
    number: string | null;
    kind: string;
    status: string;
    issue_date: string | null;
    due_date: string | null;
    /** When it was started — the one date a draft has. Absent from an older API. */
    created_at?: string;
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
  /** The issue date, or for a draft the day it was started — in the user's own date order. */
  function invoiceDate(invoice: PanelInvoice): string | null {
    if (invoice.issue_date) return fmtNumericDate(invoice.issue_date);
    if (invoice.created_at) {
      return t("invoicing.panel.created_on", { date: fmtNumericDate(invoice.created_at) });
    }
    return null;
  }

  // A draft has no number yet, so its status stands in as its name — and then it must not be
  // chipped beside itself as well ("Concept — Concept" was five rows of one word twice over).
  // Overdue is the one claim on this card, and it is drawn as a state rather than a status.
  function invoiceTitle(invoice: PanelInvoice): string {
    return invoice.number ?? t(`invoicing.status.${invoice.status}`);
  }
  function invoiceChip(invoice: PanelInvoice): string | null {
    if (invoice.overdue) return t("invoicing.status.overdue");
    return invoice.number ? t(`invoicing.status.${invoice.status}`) : null;
  }
  function outstanding(invoice: PanelInvoice): string | null {
    if (invoice.status !== "open") return null;
    if (Number(invoice.outstanding) === Number(invoice.total)) return null;
    return `${money(invoice.outstanding, invoice.currency)} ${t("invoicing.panel.outstanding")}`;
  }
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
            <PanelRow
              href={fromHref(`/invoices/${invoice.id}`, page.url)}
              title={invoiceTitle(invoice)}
              meta={invoiceDate(invoice)}
              value={money(invoice.total, invoice.currency)}
              valueMeta={outstanding(invoice)}
              chip={invoiceChip(invoice)}
              chipState={invoice.overdue ? "late" : "neutral"}
            />
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
  {#if quotes.length > 0}
    <p class="mt-4 text-xs font-medium text-text-muted">{t("invoicing.panel.quotes")}</p>
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
            <PanelRow
              href={fromHref(`/quotes/${quote.id}`, page.url)}
              title={quote.number ?? t(`invoicing.quote_status.${quote.status}`)}
              value={money(quote.total, quote.currency)}
              chip={quote.number ? t(`invoicing.quote_status.${quote.status}`) : null}
            />
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
{/if}
