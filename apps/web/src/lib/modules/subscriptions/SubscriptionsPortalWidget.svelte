<script lang="ts">
  /**
   * "Mijn abonnementen" — the client's recurring agreements on their own homepage.
   *
   * What they pay for, what it costs per period, and when the next invoice comes: the three
   * facts a client asks their agency about, read on the selected company through the portal
   * repository (a draft never leaves the API, `Subscription.__portal_horizon_clause__`). Each
   * row opens the agreement's own page, which is the read-only detail every login may open —
   * the staff list edits in a modal, and a client has nothing to edit.
   */
  import { dateLocale, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    name: string;
    status: string;
    amount: string | number | null;
    currency: string;
    interval: string;
    interval_count: number;
    next_invoice_date: string | null;
  }
  interface Payload {
    items: Row[];
    total: number;
    companyId: string | null;
  }
  const EMPTY: Payload = { items: [], total: 0, companyId: null };
  const payload = $derived((data ?? EMPTY) as Payload);
  const href = $derived(
    payload.companyId ? `/subscriptions?company=${payload.companyId}` : "/subscriptions",
  );

  function money(row: Row): string {
    if (row.amount == null) return "—";
    return new Intl.NumberFormat(dateLocale(), {
      style: "currency",
      currency: row.currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(row.amount));
  }
  function statusClass(status: string): string {
    if (status === "active") return "bg-emerald-50 text-emerald-700";
    if (status === "paused") return "bg-amber-50 text-amber-700";
    return "bg-surface text-text-muted";
  }
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.subscriptions.portal")}
  {href}
  linkLabel={t("nav.subscriptions")}
>
  {#if payload.items.length === 0}
    <p class="text-sm text-text-muted">{t("subscriptions.portal.empty")}</p>
  {:else}
    <PanelRows rows={payload.items} collapsed={6} total={payload.total} {href}>
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as sub (sub.id)}
            <li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <a
                href={`/subscriptions/${sub.id}`}
                class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
                >{sub.name}</a
              >
              <span class="text-sm tabular-nums text-text">
                {money(sub)} · {t(`subscriptions.interval.${sub.interval}`)}
              </span>
              {#if sub.next_invoice_date}
                <span class="text-xs text-text-muted">
                  {t("subscriptions.field.next_invoice")}: {fmtNumericDate(sub.next_invoice_date)}
                </span>
              {/if}
              <span class="rounded-md px-2 py-0.5 text-xs {statusClass(sub.status)}"
                >{t(`subscriptions.status.${sub.status}`)}</span
              >
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
</DashboardWidgetCard>
