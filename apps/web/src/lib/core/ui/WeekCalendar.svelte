<script lang="ts">
  /**
   * Week view for the shared calendar (#155): seven day columns sharing one time axis —
   * timed events at their actual hour, all-day items in the pinned top row. Mobile-first:
   * <sm gets the shared agenda list, showing every day (a week is short enough that hiding
   * empty days loses "my week" context).
   *
   * Drag-to-reschedule (#106) lives on the all-day chips inside the grid, day-granular,
   * exactly like the month grid.
   */
  import { eventsByDayMap } from "$lib/core/calendar";
  import { t } from "$lib/core/i18n";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
  import TimeGrid from "$lib/core/ui/TimeGrid.svelte";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    days,
    events,
    today,
    onmove,
  }: {
    /** 7 Monday-start date-only ISO strings. */
    days: string[];
    events: CalendarEvent[];
    today: string;
    /** Reschedule callback for a dropped chip (#106); absent = the grid stays read-only. */
    onmove?: (event: CalendarEvent, deltaDays: number) => void;
  } = $props();

  const eventsByDay = $derived(eventsByDayMap(days, events));
</script>

<!-- ≥sm: the week time grid -->
<div class="hidden sm:block">
  <TimeGrid {days} {events} {today} {onmove} />
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
