<script lang="ts">
  /** Weekly timesheet grid: one row per client · project · task, columns are the 7 days. */
  import { fmtWeekdayDay } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { formatMinutes } from "$lib/modules/time/format";

  interface Option {
    id: string;
    name?: string;
    title?: string;
  }

  interface Row {
    company_id: string | null;
    project_id: string | null;
    task_id: string | null;
    minutes: number[];
    total: number;
  }

  interface Week {
    week_start: string;
    days: string[];
    rows: Row[];
    day_totals: number[];
    total: number;
  }

  let {
    week,
    companies,
    projects,
    tasks,
    weekView = "full",
  }: {
    week: Week;
    companies: Option[];
    projects: Option[];
    tasks: Option[];
    /** "work" shows Mon–Fri only; totals then reflect the visible days. */
    weekView?: "full" | "work";
  } = $props();

  // Workweek = first 5 day columns; totals recomputed from what's shown so the columns add up.
  const dayCount = $derived(weekView === "work" ? 5 : week.days.length);
  const visibleDays = $derived(week.days.slice(0, dayCount));
  const sum = (nums: number[]) => nums.reduce((a, b) => a + b, 0);

  function rowLabel(row: Row): string {
    const parts = [
      companies.find((c) => c.id === row.company_id)?.name,
      projects.find((p) => p.id === row.project_id)?.name,
      tasks.find((task) => task.id === row.task_id)?.title,
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("time.general");
  }

  interface VisibleRow extends Row {
    visibleMinutes: number[];
    visibleTotal: number;
  }
  const sorted = $derived<VisibleRow[]>(
    week.rows
      .map((row) => {
        const visibleMinutes = row.minutes.slice(0, dayCount);
        return { ...row, visibleMinutes, visibleTotal: sum(visibleMinutes) };
      })
      // Drop rows that have no time in the visible window (e.g. weekend-only in workweek view).
      .filter((row) => row.visibleTotal > 0)
      .sort((a, b) => b.visibleTotal - a.visibleTotal),
  );
  const dayTotals = $derived(week.day_totals.slice(0, dayCount));
  const grandTotal = $derived(sum(dayTotals));
</script>

<section class="overflow-hidden rounded-xl border border-neutral-200 bg-white">
  <h2
    class="border-b border-neutral-100 bg-neutral-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-neutral-500"
  >
    {t("time.timesheet.heading")}
  </h2>
  {#if sorted.length === 0}
    <p class="p-6 text-sm text-neutral-500">{t("time.timesheet.empty")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-neutral-100 text-left text-xs text-neutral-400">
            <th class="px-4 py-2 font-medium">{t("time.timesheet.row")}</th>
            {#each visibleDays as day (day)}
              <th class="px-2 py-2 text-right font-medium capitalize">{fmtWeekdayDay(day)}</th>
            {/each}
            <th class="px-4 py-2 text-right font-medium">{t("time.timesheet.total")}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-50">
          {#each sorted as row (row.company_id + "|" + row.project_id + "|" + row.task_id)}
            <tr>
              <td class="max-w-[16rem] truncate px-4 py-2 font-medium text-neutral-800"
                >{rowLabel(row)}</td
              >
              {#each row.visibleMinutes as minutes, i (i)}
                <td
                  class="px-2 py-2 text-right tabular-nums {minutes
                    ? 'text-neutral-800'
                    : 'text-neutral-300'}"
                >
                  {minutes ? formatMinutes(minutes) : "·"}
                </td>
              {/each}
              <td class="px-4 py-2 text-right font-semibold tabular-nums text-neutral-900"
                >{formatMinutes(row.visibleTotal)}</td
              >
            </tr>
          {/each}
        </tbody>
        <tfoot>
          <tr class="border-t border-neutral-200 bg-neutral-50/60">
            <td class="px-4 py-2 text-xs font-semibold text-neutral-500"
              >{t("time.timesheet.total")}</td
            >
            {#each dayTotals as minutes, i (i)}
              <td
                class="px-2 py-2 text-right text-xs font-semibold tabular-nums {minutes
                  ? 'text-neutral-800'
                  : 'text-neutral-300'}"
              >
                {minutes ? formatMinutes(minutes) : "·"}
              </td>
            {/each}
            <td class="px-4 py-2 text-right text-sm font-bold tabular-nums text-neutral-900"
              >{formatMinutes(grandTotal)}</td
            >
          </tr>
        </tfoot>
      </table>
    </div>
  {/if}
</section>
