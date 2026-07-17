<script lang="ts">
  /** My Day widget: open quotes — the pipeline waiting on a client's yes. */
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface Summary {
    quotes_open_count: number;
    quotes_open_total: number;
  }
  const summary = $derived((data ?? { quotes_open_count: 0, quotes_open_total: 0 }) as Summary);
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.invoicing.quotes_open")}
  href="/quotes"
  linkLabel={t("invoicing.quotes")}
>
  <a href="/quotes" class="block text-2xl font-semibold text-text hover:text-brand">
    {fmtMoney(summary.quotes_open_total)}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    {t("invoicing.widget.quotes_open_count", { count: summary.quotes_open_count })}
  </p>
</DashboardWidgetCard>
