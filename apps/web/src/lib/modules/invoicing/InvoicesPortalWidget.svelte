<script lang="ts">
  /**
   * "Mijn facturen" — the client's invoices on their own homepage.
   *
   * Two things a client wants from an invoice tile, in this order: what is still **open**
   * (loudly red when overdue — docs/UX.md principle 4), and the recent ones behind it. Read on
   * the selected company through the portal repository, which never serves a draft
   * (`Invoice.__portal_horizon_clause__`, #266). Every row opens the document, where the pay
   * control lives when the agency offers one.
   */
  import { dateLocale, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { docStatus } from "./types";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    number: string | null;
    kind: string;
    status: string;
    overdue: boolean;
    credited?: boolean;
    fully_credited?: boolean;
    issue_date: string | null;
    due_date: string | null;
    total: string | number;
    outstanding: string | number;
    currency: string;
  }
  interface Payload {
    open: Row[];
    openTotal: number;
    recent: Row[];
    recentTotal: number;
    companyId: string | null;
  }
  const EMPTY: Payload = { open: [], openTotal: 0, recent: [], recentTotal: 0, companyId: null };
  const payload = $derived((data ?? EMPTY) as Payload);
  const href = $derived(payload.companyId ? `/invoices?company=${payload.companyId}` : "/invoices");
  const openHref = $derived(
    payload.companyId
      ? `/invoices?company=${payload.companyId}&status=open`
      : "/invoices?status=open",
  );

  const money = (value: string | number, currency: string) =>
    new Intl.NumberFormat(dateLocale(), {
      style: "currency",
      currency: currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(value));
</script>

{#snippet rows(list: Row[], showOutstanding: boolean)}
  <ul class="divide-y divide-border">
    {#each list as invoice (invoice.id)}
      {@const badge = docStatus(invoice)}
      <li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
        <a
          href={`/invoices/${invoice.id}`}
          class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
          >{invoice.number ?? "—"}</a
        >
        {#if invoice.due_date}
          <span
            class="text-xs {invoice.overdue ? 'font-semibold text-red-600' : 'text-text-muted'}"
          >
            {t("invoicing.field.due_date")}: {fmtNumericDate(invoice.due_date)}
          </span>
        {:else if invoice.issue_date}
          <span class="text-xs text-text-muted">{fmtNumericDate(invoice.issue_date)}</span>
        {/if}
        <span class="text-sm tabular-nums text-text">
          {money(showOutstanding ? invoice.outstanding : invoice.total, invoice.currency)}
        </span>
        <span
          class="rounded-md px-2 py-0.5 text-xs {badge.tone === 'danger'
            ? 'bg-red-50 text-red-700'
            : 'bg-surface text-text-muted'}">{t(badge.key)}</span
        >
      </li>
    {/each}
  </ul>
{/snippet}

<DashboardWidgetCard
  title={t("dashboard.widget.invoicing.portal")}
  {href}
  linkLabel={t("nav.invoices")}
>
  {#if payload.open.length === 0 && payload.recent.length === 0}
    <p class="text-sm text-text-muted">{t("invoicing.portal.empty")}</p>
  {:else}
    {#if payload.open.length > 0}
      <section>
        <h3 class="mb-1 text-sm font-semibold text-text">
          {t("invoicing.portal.open")}
          <span class="text-xs font-normal tabular-nums text-text-muted">({payload.openTotal})</span
          >
        </h3>
        <PanelRows rows={payload.open} collapsed={5} total={payload.openTotal} href={openHref}>
          {#snippet children(shown)}
            {@render rows(shown, true)}
          {/snippet}
        </PanelRows>
      </section>
    {/if}
    {#if payload.recent.length > 0}
      <section class:mt-4={payload.open.length > 0}>
        <h3 class="mb-1 text-sm font-semibold text-text-muted">
          {t("invoicing.portal.recent")}
        </h3>
        <PanelRows rows={payload.recent} collapsed={5} total={payload.recentTotal} {href}>
          {#snippet children(shown)}
            {@render rows(shown, false)}
          {/snippet}
        </PanelRows>
      </section>
    {/if}
  {/if}
</DashboardWidgetCard>
