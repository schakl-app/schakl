<script lang="ts">
  /**
   * Day view for the shared calendar (#155): a one-column time grid — timed events at their
   * actual hour, all-day items pinned on top. Below `sm` the shared agenda list stays: a
   * phone is for reading the day, not for studying a 24-hour axis.
   */
  import { eventsByDayMap } from "$lib/core/calendar";
  import { t } from "$lib/core/i18n";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
  import TimeGrid from "$lib/core/ui/TimeGrid.svelte";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    date,
    events,
    today,
    onmove,
  }: {
    date: string;
    events: CalendarEvent[];
    today: string;
    /** Reschedule callback for a dropped all-day chip (#106); absent = read-only. */
    onmove?: (event: CalendarEvent, deltaDays: number) => void;
  } = $props();

  const days = $derived([date]);
  const eventsByDay = $derived(eventsByDayMap(days, events));
</script>

<div class="hidden sm:block">
  <TimeGrid {days} {events} {today} {onmove} />
</div>

<div class="sm:hidden">
  <AgendaList
    {days}
    {eventsByDay}
    {today}
    showEmptyDays={true}
    emptyMessage={t("calendar.empty.day")}
  />
</div>
