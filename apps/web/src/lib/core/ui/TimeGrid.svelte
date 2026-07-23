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
   * Drag-to-reschedule (#106) stays day-granular and lives on the all-day chips, exactly as
   * on the month grid; timed blocks are not draggable (in v1 only Google events carry times,
   * and we never write to Google).
   */
  import { eventChipParts, eventLinkAttrs, eventsByDayMap, isoDiffDays } from "$lib/core/calendar";
  import { fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { getTimeZone } from "$lib/core/timezone";
  import type { CalendarEvent } from "$lib/core/registry";

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
    /** Reschedule callback for a dropped all-day chip (#106); absent = read-only. */
    onmove?: (event: CalendarEvent, deltaDays: number) => void;
  } = $props();

  const HOUR_PX = 48;
  const MORNING_SCROLL = 7 * HOUR_PX;

  const timed = $derived(events.filter((e) => e.startsAt && e.endsAt));
  const allDay = $derived(events.filter((e) => !e.startsAt || !e.endsAt));
  const allDayByDay = $derived(eventsByDayMap(days, allDay));

  /** An instant's local calendar day + minute-of-day, in the org zone (§8). */
  function localParts(iso: string): { day: string; minutes: number } {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: getTimeZone(),
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(iso));
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
    return {
      day: `${get("year")}-${get("month")}-${get("day")}`,
      minutes: Number(get("hour")) * 60 + Number(get("minute")),
    };
  }

  interface Block {
    event: CalendarEvent;
    startMin: number;
    endMin: number;
    lane: number;
    lanes: number;
  }

  /** Greedy lane assignment per overlap cluster, so concurrent events sit side by side. */
  function layoutDay(day: string): Block[] {
    const blocks = timed
      .map((event) => {
        const start = localParts(event.startsAt!);
        const end = localParts(event.endsAt!);
        // Clip a span to this column: before/after this day → 00:00 / 24:00.
        const startMin = start.day === day ? start.minutes : start.day < day ? 0 : null;
        const endMin = end.day === day ? end.minutes : end.day > day ? 24 * 60 : null;
        if (startMin === null || endMin === null || endMin <= 0) return null;
        return {
          event,
          startMin,
          endMin: Math.max(endMin, startMin + 20), // a 5-minute event still needs a label
          lane: 0,
          lanes: 1,
        };
      })
      .filter((block): block is Block => block !== null)
      .sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);

    const laneEnds: number[] = [];
    let cluster: Block[] = [];
    let clusterEnd = -1;
    for (const block of blocks) {
      if (block.startMin >= clusterEnd && cluster.length) {
        for (const done of cluster) done.lanes = laneEnds.length;
        cluster = [];
        laneEnds.length = 0;
      }
      let lane = laneEnds.findIndex((end) => end <= block.startMin);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(0);
      }
      laneEnds[lane] = block.endMin;
      block.lane = lane;
      cluster.push(block);
      clusterEnd = Math.max(clusterEnd, block.endMin);
    }
    for (const done of cluster) done.lanes = laneEnds.length;
    return blocks;
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

  // --- all-day chip drag (#106), day-granular like the month grid -------------
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
  <!-- Column headers (week view); the day view's single date lives in the page toolbar. -->
  {#if days.length > 1}
    <div
      class="grid border-b border-border"
      style="grid-template-columns: 3rem repeat({days.length}, 1fr)"
    >
      <div></div>
      {#each days as day (day)}
        <p
          class="border-l border-border p-2 text-xs font-medium {day === today
            ? 'font-bold text-brand'
            : 'text-text-muted'}"
        >
          <span class="capitalize">{fmtWeekdayShort(day)}</span>
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
      <!-- Mouse-only accelerator; the chip link stays the accessible path (see MonthCalendar). -->
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
              title={event.title}
              draggable={Boolean(event.draggable && onmove)}
              ondragstart={(e) => dragStart(e, event, day)}
              ondragend={() => (dragging = null)}
            >
              {#if event.tentative}?{/if}
              {event.title}
            </a>
          {:else}
            <span class={parts.class} style={parts.style} title={event.title}>
              {#if event.tentative}?{/if}
              {event.title}
            </span>
          {/if}
        {/each}
      </div>
    {/each}
  </div>

  <!-- The 24-hour grid, scrolled to the morning. -->
  <div bind:this={scroller} class="max-h-[34rem] overflow-y-auto">
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
        <div class="relative border-l border-border">
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
                class={parts.class}
                style="top: {top}px; height: {height}px; left: {block.lane *
                  width}%; width: {width}%; {parts.style}"
                title={block.event.title}
              >
                {#if block.event.tentative}?{/if}
                {block.event.title}
              </a>
            {:else}
              <span
                class={parts.class}
                style="top: {top}px; height: {height}px; left: {block.lane *
                  width}%; width: {width}%; {parts.style}"
                title={block.event.title}
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
