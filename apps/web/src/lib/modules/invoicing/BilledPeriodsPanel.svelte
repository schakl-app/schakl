<script lang="ts">
  /**
   * Which periods of this domain or agreement a document already holds, and on which one —
   * the record's own invoicing history, read off the claim tables the renewal and cycle crons
   * consult (`GET /invoicing/billed-periods`).
   *
   * Registered by `invoicing` onto the domain and subscription pages rather than drawn by them
   * (CLAUDE.md §6): a domain page that read `invoice_domain_periods` itself would be one module
   * reading another's table, and a tenant without invoicing never renders this. Newest period
   * first — "was last year billed?" is the question, and the first row answers it. A draft has
   * no number yet, so the row says so instead of printing a blank.
   */
  import { fmtNumericDate, fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  interface BilledPeriod {
    period_start?: string | null;
    period_end: string;
    invoice_id: string;
    invoice_number?: string | null;
    invoice_status: string;
    invoice_kind: string;
    issue_date?: string | null;
  }

  let {
    data,
  }: {
    data: unknown;
    context: EntityPanelContext;
    lookups: EntityPanelLookups;
  } = $props();

  // The registry hands every panel an opaque `data`; this panel's own `load` shapes it.
  const items = $derived((data as { items?: BilledPeriod[] } | null)?.items ?? []);

  /** A period is printed as the span it covers; a claim with no start (a pre-provenance row)
   *  as the day it ends. */
  const span = (row: BilledPeriod) =>
    row.period_start ? fmtPeriod(row.period_start, row.period_end) : fmtNumericDate(row.period_end);

  const meta = (row: BilledPeriod) => {
    const number = row.invoice_number
      ? t("invoicing.billed_periods.on_invoice", { number: row.invoice_number })
      : t("invoicing.billed_periods.on_draft");
    return row.issue_date ? `${number} · ${fmtNumericDate(row.issue_date)}` : number;
  };
</script>

{#if items.length === 0}
  <p class="text-sm text-text-muted">{t("invoicing.billed_periods.empty")}</p>
{:else}
  <PanelRows rows={items} collapsed={5}>
    {#snippet children(rows)}
      {#each rows as row (row.invoice_id + row.period_end)}
        <PanelRow
          href="/invoices/{row.invoice_id}"
          title={span(row)}
          meta={meta(row)}
          chip={t(`invoicing.status.${row.invoice_status}`)}
        />
      {/each}
    {/snippet}
  </PanelRows>
{/if}
