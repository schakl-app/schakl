<script lang="ts">
  /**
   * The day being planned, one column per person, with the block about to be booked drawn over
   * whatever is already there — so "does this clash?" is answered by looking rather than by
   * saving and finding out (docs/UX.md, the scheduling-dialog entry).
   *
   * Reads `GET /calendar/busy` (the API's `tasks/schedules/busy`, `app/core/busy.py`): every
   * calendar the instance can consult in one answer, titled only where the caller may read the
   * row behind it. A colleague's Google appointment therefore reads "Bezet 10:00–11:00" and
   * nothing more — Google's own free/busy rule — and this component draws what it is handed,
   * never deciding for itself what a viewer may know.
   *
   * The geometry is the Agenda's (`timegrid-layout`): same clipping, same lane packing, so a
   * block sits here exactly where it sits on the day view. The hour range is the working day
   * stretched to fit whatever is on it, never clipped: an 07:00 flight that fell off the top
   * would be the one conflict the view existed to show.
   */
  import { untrack } from "svelte";

  import { capitalizeFirst, fmtDayMonth, fmtWeekdayShort, RANGE_DASH } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { stateChipClass } from "$lib/core/state";
  import { clipToDay, packLanes, type Lane } from "$lib/core/ui/timegrid-layout";

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
  }
  interface BusyItem {
    user_id: string;
    starts_at: string;
    ends_at: string;
    source: string;
    kind: string;
    all_day: boolean;
    tentative: boolean;
    title: string | null;
    ref: string | null;
    href: string | null;
  }
  interface BusyFeed {
    items: BusyItem[];
    sources: string[];
    unavailable: string[];
  }

  let {
    day,
    startTime,
    durationMinutes,
    personIds,
    members = [],
    currentUserId,
    excludeRef = null,
    endpoint = "/calendar/busy",
  }: {
    /** The org-local day being planned (`yyyy-mm-dd`). */
    day: string;
    /** "HH:MM" — the proposed start; the ghost block follows it without a refetch. */
    startTime: string;
    durationMinutes: number | null;
    personIds: string[];
    members?: Member[];
    currentUserId: string;
    /** The block being edited, left out of its own conflict check (its `ref`). */
    excludeRef?: string | null;
    endpoint?: string;
  } = $props();

  const HOUR_PX = 40;
  const WORKDAY_START = 7 * 60;
  const WORKDAY_END = 19 * 60;

  let feed = $state<BusyFeed | null>(null);
  let loading = $state(false);
  let failed = $state<"forbidden" | "error" | null>(null);

  const memberName = $derived(new Map(members.map((m) => [m.user_id, memberLabel(m)])));
  const people = $derived(
    personIds.map((id) => ({
      id,
      name: id === currentUserId ? t("tasks.schedule.you") : (memberName.get(id) ?? "—"),
    })),
  );

  // One request per (day, people) pair, debounced: typing a date or adding a chip must not fire
  // a call per keystroke, and the ghost block (start/length) moves without asking the API at all
  // — the calendars did not change, only the proposal did.
  const requestKey = $derived(`${day}|${[...personIds].sort().join(",")}`);
  $effect(() => {
    const key = requestKey;
    const [forDay, joined] = key.split("|");
    const ids = joined ? joined.split(",") : [];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(forDay) || ids.length === 0) {
      untrack(() => {
        feed = null;
        failed = null;
      });
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      untrack(() => (loading = true));
      try {
        const query = new URLSearchParams({ date: forDay });
        for (const id of ids) query.append("user_ids", id);
        const res = await fetch(`${endpoint}?${query}`, {
          headers: { accept: "application/json" },
        });
        if (cancelled) return;
        if (!res.ok) {
          failed = res.status === 403 ? "forbidden" : "error";
          feed = null;
        } else {
          failed = null;
          feed = (await res.json()) as BusyFeed;
        }
      } catch {
        if (!cancelled) {
          failed = "error";
          feed = null;
        }
      } finally {
        if (!cancelled) loading = false;
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  });

  // --- geometry ---------------------------------------------------------------------------- //
  const proposal = $derived.by(() => {
    const [h, m] = startTime.split(":").map(Number);
    if (Number.isNaN(h) || Number.isNaN(m)) return null;
    const startMin = h * 60 + m;
    const endMin = Math.min(24 * 60, startMin + Math.max(1, durationMinutes ?? 0));
    return { startMin, endMin };
  });

  interface Placed extends Lane {
    item: BusyItem;
    conflict: boolean;
  }
  interface Column {
    id: string;
    name: string;
    away: (BusyItem & { conflict: boolean })[];
    blocks: Placed[];
    conflicts: string[];
  }

  function label(item: BusyItem): string {
    if (item.title) return item.title;
    return item.kind === "away" ? t("tasks.schedule.busy.away") : t("tasks.schedule.busy.busy");
  }

  function overlaps(
    a: { startMin: number; endMin: number },
    b: { startMin: number; endMin: number },
  ) {
    return a.startMin < b.endMin && a.endMin > b.startMin;
  }

  const columns = $derived.by((): Column[] => {
    const items = (feed?.items ?? []).filter((item) => !excludeRef || item.ref !== excludeRef);
    return people.map((person) => {
      const mine = items.filter((item) => item.user_id === person.id);
      const away = mine
        .filter((item) => item.all_day)
        .map((item) => ({ ...item, conflict: proposal !== null }));
      const timed = mine
        .filter((item) => !item.all_day)
        .map((item) => {
          const span = clipToDay(item.starts_at, item.ends_at, day);
          if (!span) return null;
          return {
            item,
            ...span,
            lane: 0,
            lanes: 1,
            conflict: proposal !== null && overlaps(span, proposal),
          } satisfies Placed;
        })
        .filter((block): block is Placed => block !== null);
      const blocks = packLanes(timed);
      const conflicts = [
        ...away.filter((a) => a.conflict).map((a) => label(a)),
        ...blocks
          .filter((b) => b.conflict)
          .map((b) => `${label(b.item)} ${clock(b.startMin)}${RANGE_DASH}${clock(b.endMin)}`),
      ];
      return { id: person.id, name: person.name, away, blocks, conflicts };
    });
  });

  // The visible hours: the working day, stretched to fit the proposal and everything drawn.
  const range = $derived.by(() => {
    let from = WORKDAY_START;
    let to = WORKDAY_END;
    if (proposal) {
      from = Math.min(from, proposal.startMin);
      to = Math.max(to, proposal.endMin);
    }
    for (const column of columns) {
      for (const block of column.blocks) {
        from = Math.min(from, block.startMin);
        to = Math.max(to, block.endMin);
      }
    }
    from = Math.floor(from / 60) * 60;
    to = Math.min(24 * 60, Math.ceil(to / 60) * 60);
    return { from, to };
  });
  const hours = $derived(
    Array.from({ length: (range.to - range.from) / 60 }, (_, i) => range.from / 60 + i),
  );
  const gridHeight = $derived(((range.to - range.from) / 60) * HOUR_PX);

  function top(minutes: number): number {
    return ((minutes - range.from) / 60) * HOUR_PX;
  }
  function clock(minutes: number): string {
    const m = Math.min(minutes, 24 * 60 - 1);
    return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  }

  const totalConflicts = $derived(columns.reduce((n, c) => n + c.conflicts.length, 0));

  const SOURCE_KEY: Record<string, string> = {
    "tasks.schedule": "tasks.schedule.busy.source_tasks",
    leave: "tasks.schedule.busy.source_leave",
    "google.calendar": "tasks.schedule.busy.source_google",
  };
  function sourceLabel(source: string): string {
    const key = SOURCE_KEY[source];
    return key ? t(key) : source;
  }
  const consulted = $derived(
    (feed?.sources ?? []).filter((s) => !(feed?.unavailable ?? []).includes(s)),
  );
</script>

<section class="rounded-lg border border-border" aria-label={t("tasks.schedule.busy.title")}>
  <!-- Verdict first: the one sentence the view exists to produce, before the drawing. -->
  <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
    <p class="text-sm font-medium text-text">
      {capitalizeFirst(fmtWeekdayShort(day))}
      {fmtDayMonth(day)}
      {#if proposal}
        <span class="font-normal text-text-muted">
          · {clock(proposal.startMin)}{RANGE_DASH}{clock(proposal.endMin)}
        </span>
      {/if}
    </p>
    {#if loading && !feed}
      <span class="text-xs text-text-muted">{t("tasks.schedule.busy.loading")}</span>
    {:else if failed === "forbidden"}
      <span class="text-xs text-text-muted">{t("tasks.schedule.busy.forbidden")}</span>
    {:else if failed}
      <span class="text-xs text-amber-600 dark:text-amber-400"
        >{t("tasks.schedule.busy.failed")}</span
      >
    {:else if feed}
      <span
        class="rounded-full px-2 py-0.5 text-xs font-medium {stateChipClass(
          totalConflicts ? 'late' : 'ok',
        )}"
        data-testid="busy-verdict"
        data-conflicts={totalConflicts}
      >
        {totalConflicts
          ? t("tasks.schedule.busy.conflicts", { count: String(totalConflicts) })
          : t("tasks.schedule.busy.free")}
      </span>
    {/if}
  </div>

  {#if people.length === 0}
    <p class="px-3 py-4 text-sm text-text-muted">{t("tasks.schedule.nobody")}</p>
  {:else}
    <!-- Columns side by side, one per person; more than fit scroll sideways rather than shrink
         to unreadable — a column is a calendar, and a calendar has a minimum legible width. -->
    <div class="max-h-[22rem] overflow-auto">
      <div
        class="grid min-w-fit"
        style="grid-template-columns: 3rem repeat({people.length}, minmax(9rem, 1fr));"
      >
        <div class="sticky top-0 z-20 border-b border-border bg-surface-raised"></div>
        {#each columns as column (column.id)}
          <div
            class="sticky top-0 z-20 truncate border-b border-l border-border bg-surface-raised px-2 py-1.5 text-xs font-medium text-text"
            title={column.name}
          >
            {column.name}
            {#if column.conflicts.length}
              <span class="ml-1 rounded-full px-1.5 text-[10px] {stateChipClass('late')}">
                {column.conflicts.length}
              </span>
            {/if}
          </div>
        {/each}

        <!-- The all-day row: an absence is a band over the whole day, not a block beside others. -->
        {#if columns.some((c) => c.away.length)}
          <div class="border-b border-border bg-surface"></div>
          {#each columns as column (column.id)}
            <div class="space-y-0.5 border-b border-l border-border bg-surface px-1 py-1">
              {#each column.away as item, i (item.ref ?? `${item.source}-${i}`)}
                <div
                  class="truncate rounded px-1.5 py-0.5 text-[11px] {item.conflict
                    ? 'bg-red-100 text-red-800 ring-1 ring-red-400 dark:bg-red-950 dark:text-red-200'
                    : 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200'} {item.tentative
                    ? 'opacity-70'
                    : ''}"
                  title={label(item)}
                >
                  {label(item)}{item.tentative ? " ?" : ""}
                </div>
              {/each}
            </div>
          {/each}
        {/if}

        <!-- Hour axis -->
        <div class="relative" style="height: {gridHeight}px">
          {#each hours as hour (hour)}
            <div
              class="absolute right-1 text-[10px] text-text-muted {hour * 60 === range.from
                ? ''
                : '-translate-y-1/2'}"
              style="top: {(hour - range.from / 60) * HOUR_PX}px"
            >
              {String(hour).padStart(2, "0")}:00
            </div>
          {/each}
        </div>

        {#each columns as column (column.id)}
          <div class="relative border-l border-border" style="height: {gridHeight}px">
            {#each hours as hour (hour)}
              <div
                class="absolute inset-x-0 border-t border-border/60"
                style="top: {(hour - range.from / 60) * HOUR_PX}px"
              ></div>
            {/each}
            <!-- A whole-day absence shades the column: nothing can be planned around it. -->
            {#if column.away.length}
              <div
                class="absolute inset-0 bg-[repeating-linear-gradient(135deg,transparent_0,transparent_6px,rgb(148_163_184/0.25)_6px,rgb(148_163_184/0.25)_8px)]"
              ></div>
            {/if}
            {#each column.blocks as block, i (block.item.ref ?? `${block.item.source}-${i}`)}
              <div
                class="absolute overflow-hidden rounded border px-1 text-[11px] leading-tight {block.conflict
                  ? 'border-red-400 bg-red-100 text-red-900 dark:border-red-500 dark:bg-red-950 dark:text-red-100'
                  : block.item.title
                    ? 'border-sky-300 bg-sky-100 text-sky-900 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-100'
                    : 'border-slate-300 bg-slate-200 text-slate-700 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200'} {block
                  .item.tentative
                  ? 'border-dashed opacity-70'
                  : ''}"
                style="top: {top(block.startMin)}px; height: {((block.endMin - block.startMin) /
                  60) *
                  HOUR_PX}px; left: {(block.lane / block.lanes) * 100}%; width: {100 /
                  block.lanes}%;"
                title="{label(block.item)} · {clock(block.startMin)}{RANGE_DASH}{clock(
                  block.endMin,
                )}"
                data-testid="busy-block"
                data-conflict={block.conflict}
              >
                <span class="block truncate font-medium">{label(block.item)}</span>
                <span class="block truncate text-[10px] opacity-80">
                  {clock(block.startMin)}{RANGE_DASH}{clock(block.endMin)}
                </span>
              </div>
            {/each}
            <!-- The proposal: dashed, translucent, on top — the only thing here that is not yet
                 true, drawn so the real blocks stay legible through it; its name sits in a small
                 tag at the corner rather than across whatever it covers. -->
            {#if proposal}
              <div
                class="pointer-events-none absolute inset-x-0.5 z-10 rounded border-2 border-dashed border-brand bg-brand/15"
                style="top: {top(proposal.startMin)}px; height: {((proposal.endMin -
                  proposal.startMin) /
                  60) *
                  HOUR_PX}px;"
                data-testid="busy-proposal"
              >
                <span
                  class="absolute -top-2 right-1 rounded bg-brand px-1 text-[10px] font-medium leading-4 text-white"
                >
                  {t("tasks.schedule.busy.proposed")}
                </span>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    <!-- What each clash is, per person, in words: a red block says *that*; this says *what*. -->
    {#if totalConflicts}
      <ul class="space-y-0.5 border-t border-border px-3 py-2 text-xs text-text">
        {#each columns.filter((c) => c.conflicts.length) as column (column.id)}
          <li>
            <span class="font-medium">{column.name}:</span>
            {column.conflicts.join(" · ")}
          </li>
        {/each}
      </ul>
    {/if}

    <!-- Which calendars were consulted, and which could not be — a view with a third missing
         must never look complete. -->
    {#if feed}
      <p class="border-t border-border px-3 py-1.5 text-[11px] leading-snug text-text-muted">
        {t("tasks.schedule.busy.legend", { sources: consulted.map(sourceLabel).join(" · ") })}
        {#if feed.unavailable.length}
          <span class="text-amber-600 dark:text-amber-400">
            · {t("tasks.schedule.busy.unavailable", {
              sources: feed.unavailable.map(sourceLabel).join(", "),
            })}
          </span>
        {/if}
        {#if personIds.some((id) => id !== currentUserId)}
          · {t("tasks.schedule.busy.hint")}
        {/if}
      </p>
    {/if}
  {/if}
</section>
