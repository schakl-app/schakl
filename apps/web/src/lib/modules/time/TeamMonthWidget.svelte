<script lang="ts">
  /** Manager widget: the team's hours, billable share and omzet for the current month. */
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { formatMinutes } from "$lib/modules/time/format";

  let { data }: { data: unknown } = $props();

  interface Payload {
    minutes: number;
    billable_minutes: number;
    open_minutes: number;
    revenue_month: number;
  }
  const stats = $derived((data ?? null) as Payload | null);
  const billablePct = $derived(
    stats && stats.minutes > 0 ? Math.round((stats.billable_minutes / stats.minutes) * 100) : 0,
  );
</script>

<div class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("dashboard.team_month.title")}</h2>
    <a href="/overview" class="text-xs text-brand hover:underline">{t("nav.overview")}</a>
  </div>
  {#if !stats}
    <p class="text-sm text-text-muted">{t("dashboard.team_month.empty")}</p>
  {:else}
    <!-- Every figure opens the report it is a total of (issue #15). The overview defaults to the
         running month, which is exactly the period this tile sums; "open" carries its own status
         filter, and omzet has its own page. -->
    <div class="grid grid-cols-2 gap-3">
      <a href="/overview" class="group">
        <span class="block text-xs text-text-muted">{t("time.overview.total.minutes")}</span>
        <span class="block text-lg font-semibold tabular-nums text-text group-hover:text-brand">
          {formatMinutes(stats.minutes)}
        </span>
      </a>
      <a href="/overview" class="group">
        <span class="block text-xs text-text-muted">{t("time.overview.total.billable")}</span>
        <span class="block text-lg font-semibold tabular-nums text-text group-hover:text-brand"
          >{billablePct}%</span
        >
      </a>
      <a href="/overview/revenue" class="group">
        <span class="block text-xs text-text-muted">{t("dashboard.team_month.revenue")}</span>
        <span class="block text-lg font-semibold tabular-nums text-text group-hover:text-brand">
          {fmtMoney(stats.revenue_month)}
        </span>
      </a>
      <a href="/overview?status=open" class="group">
        <span class="block text-xs text-text-muted">{t("time.overview.total.open")}</span>
        <span
          class="block text-lg font-semibold tabular-nums group-hover:text-brand {stats.open_minutes
            ? 'text-amber-600 dark:text-amber-400'
            : 'text-text'}"
        >
          {formatMinutes(stats.open_minutes)}
        </span>
      </a>
    </div>
  {/if}
</div>
