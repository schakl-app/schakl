<script lang="ts">
  /** My Day widget: remaining vacation balance + pending requests + next approved leave. */
  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { fmtHours, hoursToDays } from "./format";
  import Card from "$lib/core/ui/Card.svelte";

  let { data }: { data: unknown } = $props();

  interface Summary {
    year: number;
    remaining_hours: string | number;
    hours_per_week: string | number;
    hours_per_day: string | number;
    pending_count: number;
    next_leave_id: string | null;
    next_leave_start: string | null;
    next_leave_end: string | null;
  }
  const summary = $derived(
    (data ?? {
      remaining_hours: 0,
      hours_per_week: 40,
      hours_per_day: 8,
      pending_count: 0,
      next_leave_id: null,
      next_leave_start: null,
      next_leave_end: null,
    }) as Summary,
  );
  const days = $derived(hoursToDays(summary.remaining_hours, summary.hours_per_day));
  // The request itself, not the page it sits on (issue #15). The year rides along because the
  // list is a year at a time: a December balance's "next leave" is often January's request, and
  // `?request=` only finds what the loaded year holds.
  const nextHref = $derived(
    summary.next_leave_id && summary.next_leave_start
      ? `/leave?year=${summary.next_leave_start.slice(0, 4)}&request=${summary.next_leave_id}`
      : "/leave",
  );
</script>

<Card kind="stat" title={t("dashboard.my_day.leave")} href="/leave" linkLabel={t("nav.leave")}>
  <!-- The balance links to the leave overview it summarizes (issue #15). -->
  <a href="/leave" class="block text-2xl font-semibold text-text hover:text-brand">
    {t("leave.widget.remaining", { hours: fmtHours(summary.remaining_hours) })}
  </a>
  <p class="mt-1 text-sm text-text-muted">
    {t("leave.widget.days_equiv", { days: fmtHours(days) })}
    {#if summary.pending_count > 0}
      · <a href="/leave?year={summary.year}" class="hover:text-brand hover:underline"
        >{t("leave.widget.pending", { count: summary.pending_count })}</a
      >
    {/if}
  </p>
  {#if summary.next_leave_start && summary.next_leave_end}
    <p class="mt-1 text-sm text-text-muted">
      <a href={nextHref} class="hover:text-brand hover:underline">
        {t("leave.widget.next", {
          from: fmtPeriod(summary.next_leave_start),
          to: fmtPeriod(summary.next_leave_end),
        })}
      </a>
    </p>
  {/if}
</Card>
