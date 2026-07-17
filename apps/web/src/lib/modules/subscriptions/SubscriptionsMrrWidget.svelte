<script lang="ts">
  /** My Day widget: recurring revenue at a glance (MRR, ARR, next invoice due). */
  import { fmtDayMonthYear, fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface Upcoming {
    subscription_id: string;
    name: string;
    company_name: string;
    next_invoice_date: string;
  }
  interface Summary {
    mrr: number;
    arr: number;
    active_count: number;
    upcoming: Upcoming[];
  }
  const summary = $derived((data ?? { mrr: 0, arr: 0, active_count: 0, upcoming: [] }) as Summary);
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.subscriptions.mrr")}
  href="/subscriptions"
  linkLabel={t("nav.subscriptions")}
>
  <a href="/subscriptions" class="block text-2xl font-semibold text-text hover:text-brand">
    {fmtMoney(summary.mrr)}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    {t("subscriptions.widget.arr_active", {
      arr: fmtMoney(summary.arr),
      count: summary.active_count,
    })}
  </p>
  {#if summary.upcoming.length > 0}
    {@const next = summary.upcoming[0]}
    <p class="mt-1 text-sm text-text-muted">
      {t("subscriptions.widget.next", {
        name: next.company_name || next.name,
        date: fmtDayMonthYear(next.next_invoice_date),
      })}
    </p>
  {/if}
</DashboardWidgetCard>
