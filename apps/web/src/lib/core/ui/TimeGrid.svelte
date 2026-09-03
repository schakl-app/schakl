<script lang="ts">
  /**
   * The day/week time grid (#155): timed events positioned at their actual hour in the org
   * timezone, all-day and date-only events pinned in a row on top. One component serves both
   * views — the day view is a one-column week.
   *
   * The grid is the full 24 hours, scrollable, auto-scrolled to the working morning: clipping
   * to business hours would need an overflow affordance for the 07:00 flight, and a scrollbar
   * is a better one than a "+1 earlier" badge. All positioning is client-side arithmetic on
   * data the page already loaded — zero extra API calls (docs/PERFORMANCE.md).
   *
   * Drag-to-reschedule (#106) stays **day-granular** — a block dropped on another column moves to
   * that day and keeps its window — and works on positioned blocks as well as all-day chips. It
   * used to be all-day chips only, which excluded exactly the thing people most want to move: a
   * free-time day is drawn per hour (#270), so the one absence an employee is entitled to shift
   * was the one the grid would not let them. A source marks an event `draggable` when an edit
   * could succeed, and the API re-prices and re-approves; dragging a block vertically to change
   * its *window* is deliberately not offered — that is a different edit, with its own snapping and
   * validation, and it lives in the request form.
   */
  import {
    eventChipParts,
    eventLinkAttrs,
    eventsByDayMap,
    eventTitleAttr,
    isoDiffDays,
  } from "$lib/core/calendar";
  import { capitalizeFirst, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { CalendarEvent } from "$lib/core/registry";
  import { clipToDay, localParts, packLanes, type Lane } from "$lib/core/ui/timegrid-layout";

  let {
    days,
    events,
    today,
    onmove,
  }: {
    /** Date-only ISO strings, one column each (1 = day view, 7 = week view). */
    days: string[];
    events: CalendarEvent[];
    today: string;
    /** Reschedule callback for a dropped chip or block (#106); absent = read-only. */
    onmove?: (event: CalendarEvent, deltaDays: number) => void;
  } = $props();

  const HOUR_PX = 48;
  const MORNING_SCROLL = 7 * HOUR_PX;

  const timed = $derived(events.filter((e) => e.startsAt && e.endsAt));
  const allDay = $derived(events.filter((e) => !e.startsAt || !e.endsAt));
  const allDayByDay = $derived(eventsByDayMap(days, allDay));

  interface Block extends Lane {
    event: CalendarEvent;
  }

  /** This day's timed events, clipped to the column and packed into lanes (`timegrid-layout`). */
  function layoutDay(day: string): Block[] {
    const blocks = timed
      .map((event) => {
        const span = clipToDay(event.startsAt!, event.endsAt!, day);
        return span ? { event, ...span, lane: 0, lanes: 1 } : null;
      })
      .filter((block): block is Block => block !== null);
    return packLanes(blocks);
  }

  const blocksByDay = $derived(Object.fromEntries(days.map((day) => [day, layoutDay(day)])));

  const nowMinutes = $derived(
    days.includes(today) ? localParts(new Date().toISOString()).minutes : null,
  );

  let scroller = $state<HTMLDivElement>();
  $effect(() => {
    if (!scroller) return;
    // Land on the working morning, or on the first event if someone starts earlier.
    const firstStart = Math.min(
      MORNING_SCROLL,
      ...days.flatMap((day) => blocksByDay[day].map((b) => (b.startMin / 60) * HOUR_PX)),
    );
    scroller.scrollTop = Math.max(0, firstStart - HOUR_PX / 2);
  });

  // --- drag (#106), day-granular like the month grid, chips *and* blocks -------------
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

  // Class + inline style so a personal custom hue rides `--evc` (#281); the positioned block
  // appends `parts.style` onto its own top/height/left/width style below.
  const chipParts = (e: CalendarEvent) => {
    const parts = eventChipParts(e);
    return {
      class: `block truncate rounded px-1.5 py-0.5 text-xs ${parts.class}`,
      style: parts.style,
    };
  };
  const blockParts = (e: CalendarEvent) => {
    const parts = eventChipParts(e);
    return {
      class: `absolute overflow-hidden rounded border border-surface px-1.5 py-0.5 text-xs ${parts.class}`,
      style: parts.style,
    };
  };

  const hours = Array.from({ length: 24 }, (_, hour) => hour);
</script>

<div class="overflow-hidden rounded-xl border border-border bg-surface-raised">
  <!--
    Header, all-day row and the hour grid all live *inside* the scroller and the first two are
    pinned with `sticky`, because they have to agree on column widths: as siblings of the
    scrolling box they were laid out over the full width while the hour grid below lost the
    scrollbar's width, so every column edge drifted a few pixels further right the further along
    the week you looked. Sharing one scroll container makes them share one content width by
    construction — nothing measures the scrollbar, and it also holds on platforms whose overlay
    scrollbars have no width to measure.
  -->
  <div bind:this={scroller} class="max-h-[38rem] overflow-y-auto">
    <!-- Both pinned rows move as one block, so neither needs to know the other's height. They
         paint over the scrolling grid, so each carries its own opaque background. -->
    <div class="sticky top-0 z-20">
      <!-- Column headers (week view); the day view's single date lives in the page toolbar. -->
      {#if days.length > 1}
        <div
          class="grid border-b border-border bg-surface-raised"
          style="grid-template-columns: 3rem repeat({days.length}, 1fr)"
        >
          <div></div>
          {#each days as day (day)}
            <p
              class="border-l border-border p-2 text-xs font-medium {day === today
                ? 'font-bold text-brand'
                : 'text-text-muted'}"
            >
              <span>{capitalizeFirst(fmtWeekdayShort(day))}</span>
              {Number(day.slice(8, 10))}
            </p>
          {/each}
        </div>
      {/if}

      <!-- All-day row: date-only feeds (leave, holidays) and Google's all-day events. -->
      <div
        class="grid border-b border-border bg-surface"
        style="grid-template-columns: 3rem repeat({days.length}, 1fr)"
      >
        <p class="p-2 text-[10px] uppercase tracking-wide text-text-muted">
          {t("calendar.all_day")}
        </p>
        {#each days as day (day)}
          <!-- Mouse-only accelerator; the chip link stays the accessible path (MonthCalendar). -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div
            class="min-h-9 min-w-0 space-y-1 border-l border-border p-1.5"
            ondragover={(e) => {
              if (dragging) e.preventDefault();
            }}
            ondrop={(e) => {
              e.preventDefault();
              drop(day);
            }}
          >
            {#each allDayByDay[day] ?? [] as event (event.id + day)}
              {@const parts = chipParts(event)}
              {#if event.href}
                <a
                  href={event.href}
                  {...eventLinkAttrs(event.href)}
                  class="{parts.class} {event.draggable && onmove ? 'cursor-grab' : ''}"
                  style={parts.style}
                  title={eventTitleAttr(event)}
                  draggable={Boolean(event.draggable && onmove)}
                  ondragstart={(e) => dragStart(e, event, day)}
                  ondragend={() => (dragging = null)}
                >
                  {#if event.tentative}?{/if}
                  {event.title}
                </a>
              {:else}
                <span class={parts.class} style={parts.style} title={eventTitleAttr(event)}>
                  {#if event.tentative}?{/if}
                  {event.title}
                </span>
              {/if}
            {/each}
          </div>
        {/each}
      </div>
    </div>

    <!-- The 24-hour grid, scrolled to the morning. -->
    <div
      class="relative grid"
      style="grid-template-columns: 3rem repeat({days.length}, 1fr); height: {24 * HOUR_PX}px"
    >
      <div class="relative">
        {#each hours as hour (hour)}
          {#if hour > 0}
            <span
              class="absolute right-1.5 -translate-y-1/2 text-[10px] tabular-nums text-text-muted"
              style="top: {hour * HOUR_PX}px"
            >
              {String(hour).padStart(2, "0")}:00
            </span>
          {/if}
        {/each}
      </div>
      {#each days as day (day)}
        <!-- The whole column is the drop target, so a block only has to land on the right *day*;
             where in the column it is dropped is deliberately ignored (see the header comment). -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="relative border-l border-border"
          ondragover={(e) => {
            if (dragging) e.preventDefault();
          }}
          ondrop={(e) => {
            e.preventDefault();
            drop(day);
          }}
        >
          {#each hours as hour (hour)}
            {#if hour > 0}
              <div
                class="absolute inset-x-0 border-t border-border/60"
                style="top: {hour * HOUR_PX}px"
              ></div>
            {/if}
          {/each}
          {#if day === today && nowMinutes !== null}
            <div
              class="absolute inset-x-0 z-10 border-t-2 border-brand"
              style="top: {(nowMinutes / 60) * HOUR_PX}px"
              aria-hidden="true"
            ></div>
          {/if}
          {#each blocksByDay[day] as block (block.event.id + day)}
            {@const top = (block.startMin / 60) * HOUR_PX}
            {@const height = ((block.endMin - block.startMin) / 60) * HOUR_PX}
            {@const width = 100 / block.lanes}
            {@const parts = blockParts(block.event)}
            {#if block.event.href}
              <a
                href={block.event.href}
                {...eventLinkAttrs(block.event.href)}
                class="{parts.class} {block.event.draggable && onmove ? 'cursor-grab' : ''}"
                style="top: {top}px; height: {height}px; left: {block.lane *
                  width}%; width: {width}%; {parts.style}"
                title={eventTitleAttr(block.event)}
                draggable={Boolean(block.event.draggable && onmove)}
                ondragstart={(e) => dragStart(e, block.event, day)}
                ondragend={() => (dragging = null)}
              >
                {#if block.event.tentative}?{/if}
                {block.event.title}
              </a>
            {:else}
              <span
                class={parts.class}
                style="top: {top}px; height: {height}px; left: {block.lane *
                  width}%; width: {width}%; {parts.style}"
                title={eventTitleAttr(block.event)}
              >
                {#if block.event.tentative}?{/if}
                {block.event.title}
              </span>
            {/if}
          {/each}
        </div>
      {/each}
    </div>
  </div>
</div>
