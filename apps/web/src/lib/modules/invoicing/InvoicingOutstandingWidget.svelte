<script lang="ts">
  /** My Day widget: outstanding invoice money — overdue loudly red (UX Principle 4). */
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface Summary {
    open_count: number;
    open_total: number;
    overdue_count: number;
    overdue_total: number;
  }
  const summary = $derived(
    (data ?? { open_count: 0, open_total: 0, overdue_count: 0, overdue_total: 0 }) as Summary,
  );
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.invoicing.outstanding")}
  href="/invoices"
  linkLabel={t("nav.invoicing")}
>
  <a href="/invoices" class="block text-2xl font-semibold text-text hover:text-brand">
    {fmtMoney(summary.open_total)}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    {t("invoicing.widget.open_count", { count: summary.open_count })}
  </p>
  {#if summary.overdue_count > 0}
    <a
      href="/invoices?overdue=1"
      class="mt-1 block text-sm font-medium text-red-600 hover:underline dark:text-red-400"
    >
      {t("invoicing.widget.overdue", {
        count: summary.overdue_count,
        total: fmtMoney(summary.overdue_total),
      })}
    </a>
  {/if}
</DashboardWidgetCard>
