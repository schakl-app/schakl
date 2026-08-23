<script lang="ts">
  /** My Day widget: outstanding invoice money — overdue loudly red (UX Principle 4). */
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";

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
  kind="stat"
  title={t("dashboard.widget.invoicing.outstanding")}
  href="/invoices"
  linkLabel={t("nav.invoicing")}
>
  <a href="/invoices" class="block text-2xl font-semibold text-text hover:text-brand">
    {fmtMoney(summary.open_total)}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    <!-- "Open" here is *owing* — several statuses, not one key — so this opens the register the
         figure is drawn from rather than a filter that would answer a different question. -->
    <a href="/invoices" class="hover:text-brand hover:underline">
      {t("invoicing.widget.open_count", { count: summary.open_count })}
    </a>
  </p>
  {#if summary.overdue_count > 0}
    <!-- Past its term is `late`, drawn from the palette rather than a hand-written red (#404):
         the glyph is what says "overdue" to a reader who cannot separate it from the muted line
         above, and one shade means this figure and the invoice list agree. -->
    <a href="/invoices?overdue=1" class="mt-1 block hover:underline">
      <StateMark
        state="late"
        label={t("invoicing.widget.overdue", {
          count: summary.overdue_count,
          total: fmtMoney(summary.overdue_total),
        })}
      />
    </a>
  {/if}
</DashboardWidgetCard>
