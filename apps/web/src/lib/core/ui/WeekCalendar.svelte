<script lang="ts">
  /**
   * Week grid for the shared calendar: one Monday-start row, events as colored chips
   * (mirrors MonthCalendar's cell markup). Mobile-first: <sm gets the shared agenda list,
   * showing every day (a week is short enough that hiding empty days loses "my week" context).
   *
   * Draggable chips (#106) mirror MonthCalendar: drop on another day → `onmove(event, delta)`;
   * the chip's own link into the edit form stays the keyboard/touch alternative.
   */
  import { eventChipClass, eventsByDayMap, isoDiffDays } from "$lib/core/calendar";
  import { fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
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

  let dragging = $state<{ event: CalendarEvent; day: string } | null>(null);

  function dragStart(e: DragEvent, event: CalendarEvent, day: string) {
    dragging = { event, day };
    if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
  }

  function drop(day: string) {
    if (!dragging) return;
    const delta = isoDiffDays(dragging.day, day);
    if (delta !== 0) onmove?.(dragging.event, delta);
    dragging = null;
  }

  // The holiday/leave distinction lives in `eventChipClass`, not here (#47).
  const chipClass = (e: CalendarEvent) =>
    `block truncate rounded px-1.5 py-0.5 text-xs ${eventChipClass(e)}`;
</script>

<!-- ≥sm: one week row -->
<div class="hidden overflow-hidden rounded-xl border border-border bg-surface-raised sm:block">
  <div class="grid grid-cols-7">
    {#each days as day, i (day)}
      {@const isToday = day === today}
      <!-- Mouse-only accelerator; the chip link stays the accessible path (see MonthCalendar). -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="min-h-56 border-b border-border p-2 {i % 7 !== 6 ? 'border-r' : ''}"
        ondragover={(e) => {
          if (dragging) e.preventDefault();
        }}
        ondrop={(e) => {
          e.preventDefault();
          drop(day);
        }}
      >
        <p class="mb-2 text-xs font-medium {isToday ? 'font-bold text-brand' : 'text-text-muted'}">
          <span class="capitalize">{fmtWeekdayShort(day)}</span>
          {Number(day.slice(8, 10))}
        </p>
        <div class="space-y-1">
          {#each eventsByDay[day] ?? [] as event (event.id + day)}
            {#if event.href}
              <a
                href={event.href}
                class="{chipClass(event)} {event.draggable && onmove ? 'cursor-grab' : ''}"
                title={event.title}
                draggable={Boolean(event.draggable && onmove)}
                ondragstart={(e) => dragStart(e, event, day)}
                ondragend={() => (dragging = null)}
              >
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
