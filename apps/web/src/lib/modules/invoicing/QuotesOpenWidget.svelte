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
  kind="stat"
  title={t("dashboard.widget.invoicing.quotes_open")}
  href="/quotes"
  linkLabel={t("invoicing.quotes")}
>
  <!-- Both figures count the *open* quotes, so both open that filter — not the whole register
       with the drafts and the ones already accepted (issue #15). -->
  <a href="/quotes?status=open" class="block text-2xl font-semibold text-text hover:text-brand">
    {fmtMoney(summary.quotes_open_total)}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    <a href="/quotes?status=open" class="hover:text-brand hover:underline">
      {t("invoicing.widget.quotes_open_count", { count: summary.quotes_open_count })}
    </a>
  </p>
</DashboardWidgetCard>
