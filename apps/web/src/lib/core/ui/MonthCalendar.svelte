<script lang="ts">
  /**
   * Month grid for the shared calendar: Monday-start weeks, events as colored chips
   * (multi-day events repeat per day). Mobile-first (docs/UX.md): small screens get a
   * per-day agenda list instead of cramped cells; ≥sm gets the full grid.
   */
  import { monthGrid } from "$lib/core/calendar";
  import { fmtLongDay, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { labelChipClass, labelDotClass } from "$lib/core/ui/colors";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    month,
    events,
    today,
  }: {
    /** "yyyy-mm" */
    month: string;
    events: CalendarEvent[];
    /** Date-only ISO of today (SSR-provided to avoid timezone drift). */
    today: string;
  } = $props();

  const days = $derived(monthGrid(month));
  const eventsByDay = $derived.by(() => {
    const byDay: Record<string, CalendarEvent[]> = {};
    for (const day of days) {
      const hits = events.filter((e) => e.start <= day && e.end >= day);
      if (hits.length) byDay[day] = hits;
    }
    return byDay;
  });
  const weekdayHeaders = $derived(days.slice(0, 7).map((d) => fmtWeekdayShort(d)));

  const chipClass = (e: CalendarEvent) =>
    `block truncate rounded px-1.5 py-0.5 text-xs ${labelChipClass(e.color)} ${
      e.tentative ? "opacity-60" : ""
    }`;
</script>

<!-- ≥sm: the month grid -->
<div class="hidden overflow-hidden rounded-xl border border-neutral-200 bg-white sm:block">
  <div class="grid grid-cols-7 border-b border-neutral-100 bg-neutral-50">
    {#each weekdayHeaders as label (label)}
      <div class="px-2 py-2 text-xs font-medium capitalize text-neutral-400">{label}</div>
    {/each}
  </div>
  <div class="grid grid-cols-7">
    {#each days as day, i (day)}
      {@const inMonth = day.slice(0, 7) === month}
      {@const isToday = day === today}
      <div
        class="min-h-24 border-b border-neutral-100 p-1.5 {i % 7 !== 6 ? 'border-r' : ''} {inMonth
          ? ''
          : 'bg-neutral-50/60'}"
      >
        <p
          class="mb-1 text-xs {isToday
            ? 'font-bold text-brand'
            : inMonth
              ? 'text-neutral-600'
              : 'text-neutral-300'}"
        >
          {Number(day.slice(8, 10))}
        </p>
        <div class="space-y-0.5">
          {#each eventsByDay[day] ?? [] as event (event.id + day)}
            {#if event.href}
              <a href={event.href} class={chipClass(event)} title={event.title}>
                {#if event.tentative}?{/if}
                {event.title}
              </a>
            {:else}
              <span class={chipClass(event)} title={event.title}>
                {#if event.tentative}?{/if}
                {event.title}
              </span>
            {/if}
          {/each}
        </div>
      </div>
    {/each}
  </div>
</div>

<!-- <sm: agenda list of the month's days that have events -->
<div class="space-y-3 sm:hidden">
  {#each days.filter((d) => d.slice(0, 7) === month && d in eventsByDay) as day (day)}
    <section class="rounded-xl border border-neutral-200 bg-white p-4">
      <h3
        class="mb-2 text-xs font-semibold capitalize {day === today
          ? 'text-brand'
          : 'text-neutral-500'}"
      >
        {fmtLongDay(day)}
      </h3>
      <ul class="space-y-1.5">
        {#each eventsByDay[day] ?? [] as event (event.id + day)}
          <li>
            <a
              href={event.href ?? "#"}
              class="flex items-center gap-2 text-sm text-neutral-800 {event.tentative
                ? 'opacity-60'
                : ''}"
            >
              <span class="h-2 w-2 shrink-0 rounded-full {labelDotClass(event.color)}"></span>
              <span class="truncate">{event.title}</span>
              {#if event.tentative}
                <span class="text-xs text-neutral-400">{t("calendar.tentative")}</span>
              {/if}
            </a>
          </li>
        {/each}
      </ul>
    </section>
  {:else}
    <p class="rounded-xl border border-neutral-200 bg-white p-6 text-sm text-neutral-500">
      {t("calendar.empty")}
    </p>
  {/each}
</div>
