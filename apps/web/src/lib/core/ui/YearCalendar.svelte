<script lang="ts">
  /**
   * Year view: 12 mini-month grids, density-shaded per day from aggregated counts only — the
   * page load never ships raw events for this view (docs/PERFORMANCE.md). A single cell can't
   * compose multiple event colors, so density uses one neutral/brand ramp instead of the
   * per-label palette. <sm collapses to 12 compact month rows (a 365-row list would not be a
   * "usable agenda list" on a phone).
   */
  import { monthGrid, type CalendarDayAggregate } from "$lib/core/calendar";
  import { monthLabels } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  let {
    year,
    aggregates,
    today,
  }: {
    /** "yyyy" */
    year: string;
    aggregates: Record<string, CalendarDayAggregate>;
    today: string;
  } = $props();

  const months = $derived(
    Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`),
  );
  const labels = $derived(monthLabels());

  function densityClass(count: number): string {
    if (count >= 7) return "bg-brand text-white";
    if (count >= 4) return "bg-brand/60 text-white";
    if (count >= 2) return "bg-brand/30 text-text";
    return "bg-brand/10 text-text";
  }

  function monthTotal(month: string): number {
    let total = 0;
    for (const [day, agg] of Object.entries(aggregates)) {
      if (day.slice(0, 7) === month) total += agg.count;
    }
    return total;
  }
</script>

<!-- ≥sm: 12 mini-month grids, density-shaded -->
<div class="hidden grid-cols-2 gap-4 sm:grid lg:grid-cols-3">
  {#each months as month, i (month)}
    <div class="rounded-xl border border-border bg-surface-raised p-3">
      <h3 class="mb-2 text-sm font-medium capitalize text-text">{labels[i]}</h3>
      <div class="grid grid-cols-7 gap-0.5">
        {#each monthGrid(month) as day (day)}
          {@const inMonth = day.slice(0, 7) === month}
          {@const agg = aggregates[day]}
          {@const isToday = day === today}
          {#if inMonth}
            <a
              href="?view=day&date={day}"
              class="flex aspect-square items-center justify-center rounded text-[10px] {agg
                ? densityClass(agg.count)
                : 'bg-surface text-text-muted'} {agg?.tentativeOnly
                ? 'border border-dashed border-border'
                : ''} {isToday ? 'ring-1 ring-brand' : ''}"
              title={agg ? t("calendar.year.count", { count: agg.count }) : undefined}
            >
              {Number(day.slice(8, 10))}
            </a>
          {:else}
            <span class="aspect-square"></span>
          {/if}
        {/each}
      </div>
    </div>
  {/each}
</div>

<!-- <sm: compact per-month rows -->
<div class="space-y-2 sm:hidden">
  {#each months as month, i (month)}
    <a
      href="?view=month&date={month}-01"
      class="flex items-center justify-between rounded-xl border border-border bg-surface-raised p-3"
    >
      <span class="text-sm font-medium capitalize text-text">{labels[i]}</span>
      <span class="text-xs text-text-muted">
        {t("calendar.year.count", { count: monthTotal(month) })}
      </span>
    </a>
  {/each}
</div>
