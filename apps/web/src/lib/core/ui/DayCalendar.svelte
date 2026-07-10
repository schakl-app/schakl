<script lang="ts">
  /**
   * Day view for the shared calendar: `CalendarEvent` is date-only (no time-of-day), so a
   * single day is just the shared agenda list at every breakpoint — no grid to build.
   */
  import { eventsByDayMap } from "$lib/core/calendar";
  import { t } from "$lib/core/i18n";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    date,
    events,
    today,
  }: {
    date: string;
    events: CalendarEvent[];
    today: string;
  } = $props();

  const days = $derived([date]);
  const eventsByDay = $derived(eventsByDayMap(days, events));
</script>

<AgendaList
  {days}
  {eventsByDay}
  {today}
  showEmptyDays={true}
  emptyMessage={t("calendar.empty.day")}
/>
