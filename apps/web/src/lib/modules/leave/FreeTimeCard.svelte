<script lang="ts">
  /**
   * The employee's free time (vrije tijd): the pot, the days it bought, and where they are.
   *
   * The ordinary balance card cannot do this job. Free days are laid down as *approved* leave, so
   * the moment the generator has placed them all, entitled and approved are equal and the card
   * reads "0 u over". That is arithmetically true and completely useless: nobody asks how many
   * free hours are unspent, they ask **when is my next day off** and **can I move it**. So this
   * card leads with the next date, lists the upcoming days, and puts verplaatsen and laten
   * vervallen on each one (docs/UX.md principle 7: a number the user cannot take apart is a
   * number they will not trust).
   *
   * Every write here self-gates on the API's own permission, because /leave is reachable by a
   * `client` portal login and living outside edit mode is not a gate (docs/UX.md, #244).
   */
  import { CalendarClock, Ban, Pencil } from "@lucide/svelte";

  import { page } from "$app/state";
  import { fmtNumericDate, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import { labelDotClass } from "$lib/core/ui/colors";

  import { fmtHours } from "./format";

  export interface FreeTimeDay {
    request_id: string;
    date: string;
    hours: string | number;
    start_time?: string | null;
    end_time?: string | null;
    from_pattern: boolean;
  }
  export interface FreeTimeOverview {
    leave_type_ids: string[];
    entitled_hours: string | number;
    placed_hours: string | number;
    taken_hours: string | number;
    upcoming_hours: string | number;
    unplaced_hours: string | number;
    overhang_hours: string | number;
    hours_per_day: string | number;
    next_date: string | null;
    days: FreeTimeDay[];
  }

  let {
    freeTime,
    color = "cyan",
    onmove,
    oncancel,
  }: {
    freeTime: FreeTimeOverview | null;
    /** The free-time leave type's own colour, so the card matches its chips on the agenda. */
    color?: string;
    /** Open the request edit modal for this free day (the accessible path to "verplaatsen"). */
    onmove?: (requestId: string) => void;
    oncancel?: (requestId: string) => void;
  } = $props();

  const perDay = $derived(Number(freeTime?.hours_per_day ?? 0));
  /** Hours as days, through the shared formatter: a raw JS number prints "11.5" in a Dutch UI. */
  const days = (hours: string | number) =>
    fmtHours(perDay > 0 ? Math.round((Number(hours) / perDay) * 10) / 10 : 0);
  /** How many whole days are still on the calendar ahead — the figure the card leads with. */
  const upcomingCount = $derived(freeTime?.days.length ?? 0);
  const canWrite = $derived(can(page.data.user, "leave.request.write"));
  // A pot of zero and nothing placed means this employee simply has no free time; the card would
  // be an empty box explaining a concept they do not have.
  const show = $derived(
    freeTime !== null &&
      freeTime.leave_type_ids.length > 0 &&
      (Number(freeTime.entitled_hours) > 0 || Number(freeTime.placed_hours) > 0),
  );

  function windowText(day: FreeTimeDay): string | null {
    if (day.start_time && day.end_time) return `${day.start_time} – ${day.end_time}`;
    if (day.start_time) return t("leave.recurring.from_time", { time: day.start_time });
    if (day.end_time) return t("leave.recurring.until_time", { time: day.end_time });
    return null;
  }
</script>

{#if show && freeTime}
  <section class="mb-6 rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-text">
        <span class="h-2.5 w-2.5 rounded-full {labelDotClass(color)}"></span>
        {t("leave.free_time.title")}
      </h2>
      {#if Number(freeTime.overhang_hours) > 0}
        <!-- A contract change reprorated the pot and left these on the calendar. The employee
             cannot fix it themselves, so the card says what happened rather than showing a
             negative number with no explanation. -->
        <span class="text-xs text-amber-600 dark:text-amber-400">
          {t("leave.free_time.overhang", { days: days(freeTime.overhang_hours) })}
        </span>
      {/if}
    </div>

    <!-- The lead is the next day off, not a balance: it is the question this card exists for. -->
    <p class="text-2xl font-semibold text-text">
      {#if freeTime.next_date}
        <span class="capitalize">{fmtWeekdayShort(freeTime.next_date)}</span>
        {fmtNumericDate(freeTime.next_date)}
      {:else}
        {t("leave.free_time.none_planned")}
      {/if}
    </p>
    <p class="mt-1 text-sm text-text-muted">
      {#if freeTime.next_date}
        {t("leave.free_time.upcoming", { count: upcomingCount })}
      {:else}
        {t("leave.free_time.none_planned_hint")}
      {/if}
    </p>

    <dl class="mt-4 grid grid-cols-3 gap-3 border-t border-border pt-3 text-sm">
      <div>
        <dt class="text-xs text-text-muted">{t("leave.free_time.entitled")}</dt>
        <dd class="font-medium text-text">
          {t("leave.free_time.days_value", { days: days(freeTime.entitled_hours) })}
        </dd>
      </div>
      <div>
        <dt class="text-xs text-text-muted">{t("leave.free_time.taken")}</dt>
        <dd class="font-medium text-text">
          {t("leave.free_time.days_value", { days: days(freeTime.taken_hours) })}
        </dd>
      </div>
      <div>
        <dt class="text-xs text-text-muted">{t("leave.free_time.unplanned")}</dt>
        <dd class="font-medium text-text">
          {t("leave.free_time.days_value", { days: days(freeTime.unplaced_hours) })}
        </dd>
      </div>
    </dl>

    {#if freeTime.days.length > 0}
      <ul class="mt-3 divide-y divide-border border-t border-border">
        {#each freeTime.days.slice(0, 8) as day (day.request_id)}
          <li class="flex items-center gap-3 py-2 text-sm">
            <CalendarClock size={14} class="shrink-0 text-text-muted" />
            <span class="min-w-0 flex-1">
              <span class="text-text">
                <span class="capitalize">{fmtWeekdayShort(day.date)}</span>
                {fmtNumericDate(day.date)}
              </span>
              <span class="ml-1 text-xs text-text-muted">
                {#if windowText(day)}{windowText(day)} ·
                {/if}
                {t("leave.free_time.day_hours", { days: days(day.hours) })}
              </span>
            </span>
            {#if canWrite && (onmove || oncancel)}
              <ActionsMenu
                compact
                items={[
                  ...(onmove
                    ? [
                        {
                          label: t("leave.free_time.move"),
                          icon: Pencil,
                          onclick: () => onmove(day.request_id),
                        },
                      ]
                    : []),
                  ...(oncancel
                    ? [
                        {
                          label: t("leave.requests.cancel"),
                          icon: Ban,
                          danger: true,
                          onclick: () => oncancel(day.request_id),
                        },
                      ]
                    : []),
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
      {#if freeTime.days.length > 8}
        <!-- Never silent truncation: a cut list that says nothing reads as "that's all of them"
             (docs/UX.md / docs/PERFORMANCE.md). -->
        <p class="mt-2 text-xs text-text-muted">
          {t("leave.free_time.more", { count: freeTime.days.length - 8 })}
        </p>
      {/if}
    {/if}
  </section>
{/if}
