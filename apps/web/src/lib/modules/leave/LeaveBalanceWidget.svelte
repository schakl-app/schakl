<script lang="ts">
  /** My Day widget: remaining vacation balance + pending requests + next approved leave. */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { fmtHours, hoursToDays } from "./format";

  let { data }: { data: unknown } = $props();

  interface Summary {
    year: number;
    remaining_hours: string | number;
    hours_per_week: string | number;
    pending_count: number;
    next_leave_start: string | null;
    next_leave_end: string | null;
  }
  const summary = $derived(
    (data ?? {
      remaining_hours: 0,
      hours_per_week: 40,
      pending_count: 0,
      next_leave_start: null,
      next_leave_end: null,
    }) as Summary,
  );
  const days = $derived(hoursToDays(summary.remaining_hours, summary.hours_per_week));
</script>

<div class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("dashboard.my_day.leave")}</h2>
    <a href="/leave" class="text-xs text-brand hover:underline">{t("nav.leave")}</a>
  </div>
  <p class="text-2xl font-semibold text-text">
    {t("leave.widget.remaining", { hours: fmtHours(summary.remaining_hours) })}
  </p>
  <p class="mt-1 text-sm text-text-muted">
    {t("leave.widget.days_equiv", { days: fmtHours(days) })}
    {#if summary.pending_count > 0}
      · {t("leave.widget.pending", { count: summary.pending_count })}
    {/if}
  </p>
  {#if summary.next_leave_start && summary.next_leave_end}
    <p class="mt-1 text-sm text-text-muted">
      {t("leave.widget.next", {
        from: fmtDayMonth(summary.next_leave_start),
        to: fmtDayMonth(summary.next_leave_end),
      })}
    </p>
  {/if}
</div>
