<script lang="ts">
  /**
   * Week grid for the shared calendar: one Monday-start row, events as colored chips
   * (mirrors MonthCalendar's cell markup). Mobile-first: <sm gets the shared agenda list,
   * showing every day (a week is short enough that hiding empty days loses "my week" context).
   */
  import { eventsByDayMap } from "$lib/core/calendar";
  import { fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
  import { labelChipClass } from "$lib/core/ui/colors";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    days,
    events,
    today,
  }: {
    /** 7 Monday-start date-only ISO strings. */
    days: string[];
    events: CalendarEvent[];
    today: string;
  } = $props();

  const eventsByDay = $derived(eventsByDayMap(days, events));

  const chipClass = (e: CalendarEvent) =>
    `block truncate rounded px-1.5 py-0.5 text-xs ${labelChipClass(e.color)} ${
      e.tentative ? "opacity-60" : ""
    }`;
</script>

<!-- ≥sm: one week row -->
<div class="hidden overflow-hidden rounded-xl border border-neutral-200 bg-white sm:block">
  <div class="grid grid-cols-7">
    {#each days as day, i (day)}
      {@const isToday = day === today}
      <div class="min-h-56 border-b border-neutral-100 p-2 {i % 7 !== 6 ? 'border-r' : ''}">
        <p class="mb-2 text-xs font-medium {isToday ? 'font-bold text-brand' : 'text-neutral-500'}">
          <span class="capitalize">{fmtWeekdayShort(day)}</span>
          {Number(day.slice(8, 10))}
        </p>
        <div class="space-y-1">
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

<!-- <sm: agenda list, every day of the week -->
<div class="sm:hidden">
  <AgendaList
    {days}
    {eventsByDay}
    {today}
    showEmptyDays={true}
    emptyMessage={t("calendar.empty.week")}
  />
</div>
