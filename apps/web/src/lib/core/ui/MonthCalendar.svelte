<script lang="ts">
  /**
   * Month grid for the shared calendar: Monday-start weeks, events as colored chips
   * (multi-day events repeat per day). Mobile-first (docs/UX.md): small screens get a
   * per-day agenda list instead of cramped cells; ≥sm gets the full grid.
   *
   * Draggable chips (#106): an event its source marked `draggable` can be dropped on another
   * day cell, which hands `(event, deltaDays)` to `onmove` — the page posts it to the owning
   * module's move handler. Clicking the chip (the deep link into the edit form) stays the
   * keyboard/touch alternative, so dragging is an accelerator, never the only way (docs/UX.md).
   */
  import { eventChipClass, eventsByDayMap, isoDiffDays, monthGrid } from "$lib/core/calendar";
  import { fmtWeekdayShort } from "$lib/core/format";
  import AgendaList from "$lib/core/ui/AgendaList.svelte";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    month,
    events,
    today,
    onmove,
  }: {
    /** "yyyy-mm" */
    month: string;
    events: CalendarEvent[];
    /** Date-only ISO of today (SSR-provided to avoid timezone drift). */
    today: string;
    /** Reschedule callback for a dropped chip (#106); absent = the grid stays read-only. */
    onmove?: (event: CalendarEvent, deltaDays: number) => void;
  } = $props();

  const days = $derived(monthGrid(month));
  const eventsByDay = $derived(eventsByDayMap(days, events));
  const weekdayHeaders = $derived(days.slice(0, 7).map((d) => fmtWeekdayShort(d)));
  const monthDays = $derived(days.filter((d) => d.slice(0, 7) === month));

  // The chip that left its cell, and the day it was picked up from — the drop's reference
  // point, because moving a multi-day event means shifting the whole span by the drag delta.
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

<!-- ≥sm: the month grid -->
<div class="hidden overflow-hidden rounded-xl border border-border bg-surface-raised sm:block">
  <div class="grid grid-cols-7 border-b border-border bg-surface">
    {#each weekdayHeaders as label (label)}
      <div class="px-2 py-2 text-xs font-medium capitalize text-text-muted">{label}</div>
    {/each}
  </div>
  <div class="grid grid-cols-7">
    {#each days as day, i (day)}
      {@const inMonth = day.slice(0, 7) === month}
      {@const isToday = day === today}
      <!-- The drop target is a mouse-only accelerator; the chip's link into the edit form is
           the accessible path, so the cell needs no interactive role (docs/UX.md: drag with
           graceful fallback). -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="min-h-24 border-b border-border p-1.5 {i % 7 !== 6 ? 'border-r' : ''} {inMonth
          ? ''
          : 'bg-surface/60'}"
        ondragover={(e) => {
          if (dragging) e.preventDefault();
        }}
        ondrop={(e) => {
          e.preventDefault();
          drop(day);
        }}
      >
        <p
          class="mb-1 text-xs {isToday
            ? 'font-bold text-brand'
            : inMonth
              ? 'text-text-muted'
              : 'text-text-muted'}"
        >
          {Number(day.slice(8, 10))}
        </p>
        <div class="space-y-0.5">
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

<!-- <sm: agenda list of the month's days that have events -->
<div class="sm:hidden">
  <AgendaList days={monthDays} {eventsByDay} {today} />
</div>
