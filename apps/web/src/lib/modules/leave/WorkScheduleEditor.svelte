<script lang="ts">
  /**
   * The weekly work schedule grid (#46): seven rows, a working-day toggle, start/end, and the
   * day's break windows as a **repeater** — a morning coffee break next to lunch is ordinary,
   * so the second break costs one click, not a support ticket.
   *
   * Posts the whole week as one hidden JSON field (docs/UX.md — one save per editing surface,
   * never one per field). Render it **outside** the parent `<form>` and hand it that form's id
   * as `formId`: the grid's `TimeInput`s each post a hidden field of their own, and a form they
   * are not inside is a form they cannot pollute. `schedule` is the only value that travels.
   * Times go through the shared `TimeInput` (24-hour, #8) — never a native `<input type=time>`.
   *
   * Copy-to-other-days matters more here than anywhere: nobody wants to enter two breaks five
   * times.
   */
  import { Copy, Plus, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import { fmtHours } from "./format";
  import {
    cloneSchedule,
    dayError,
    dayHours,
    defaultWorkDay,
    scheduleError,
    weekHours,
    WEEKDAYS,
    type Weekday,
    type WorkSchedule,
  } from "./schedule";

  let {
    schedule = $bindable(),
    name = "schedule",
    formId,
    disabled = false,
  }: {
    schedule: WorkSchedule;
    /** Hidden field name carrying the serialized week. */
    name?: string;
    /** `<form id>` to post into when the save button lives outside this component. */
    formId?: string;
    disabled?: boolean;
  } = $props();

  const dayLabel: Record<Weekday, () => string> = {
    mon: () => t("leave.schedule.mon"),
    tue: () => t("leave.schedule.tue"),
    wed: () => t("leave.schedule.wed"),
    thu: () => t("leave.schedule.thu"),
    fri: () => t("leave.schedule.fri"),
    sat: () => t("leave.schedule.sat"),
    sun: () => t("leave.schedule.sun"),
  };

  const total = $derived(weekHours(schedule));
  const error = $derived(scheduleError(schedule));
  const serialized = $derived(JSON.stringify(schedule));

  function toggleDay(day: Weekday, working: boolean) {
    schedule[day] = working ? defaultWorkDay() : null;
  }

  function addBreak(day: Weekday) {
    schedule[day]?.breaks.push({ start: "12:30", end: "13:00" });
  }

  function removeBreak(day: Weekday, index: number) {
    schedule[day]?.breaks.splice(index, 1);
  }
  // Deliberately *not* re-sorted as you type: the API stores breaks sorted and hands them back
  // that way on the next load, whereas re-ordering the rows on every committed time yanks the
  // field out from under the cursor of anyone entering a morning break second.

  /** Copy this day onto every other **working** day; a day off stays a day off. */
  function copyToOthers(source: Weekday) {
    const template = schedule[source];
    if (!template) return;
    const copy = cloneSchedule(schedule);
    for (const day of WEEKDAYS) {
      if (day === source || copy[day] === null) continue;
      copy[day] = { ...template, breaks: template.breaks.map((b) => ({ ...b })) };
    }
    schedule = copy;
  }
</script>

<input type="hidden" {name} value={serialized} form={formId} />

<div class="space-y-2">
  {#each WEEKDAYS as day (day)}
    {@const value = schedule[day]}
    {@const problem = dayError(value)}
    <div
      class="rounded-lg border px-3 py-2 {problem
        ? 'border-red-300 dark:border-red-800'
        : 'border-border'} bg-surface-raised"
    >
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <label class="flex w-28 shrink-0 items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            checked={value !== null}
            {disabled}
            onchange={(e) => toggleDay(day, e.currentTarget.checked)}
            class="h-4 w-4 rounded border-border"
          />
          <span class="font-medium">{dayLabel[day]()}</span>
        </label>

        {#if value}
          <div class="flex items-center gap-2">
            <!-- `shrink-0`: a flex item's min-width is its content, so a squeezed row would clip
                 "12:30" to "12:3" behind the clock icon rather than wrap. -->
            <div class="w-24 shrink-0">
              <TimeInput
                id="{name}-{day}-start"
                name="{name}-{day}-start"
                bind:value={value.start}
              />
            </div>
            <span class="text-text-muted">–</span>
            <div class="w-24 shrink-0">
              <TimeInput id="{name}-{day}-end" name="{name}-{day}-end" bind:value={value.end} />
            </div>
          </div>

          <span class="ml-auto flex items-center gap-2">
            <span class="text-sm tabular-nums text-text-muted">
              {t("leave.schedule.day_hours", { hours: fmtHours(dayHours(value)) })}
            </span>
            <button
              type="button"
              {disabled}
              class="rounded p-1 text-text-muted hover:text-brand"
              title={t("leave.schedule.copy_to_others")}
              aria-label={t("leave.schedule.copy_to_others")}
              onclick={() => copyToOthers(day)}
            >
              <Copy size={14} />
            </button>
          </span>
        {:else}
          <span class="ml-auto text-sm text-text-muted">{t("leave.schedule.day_off")}</span>
        {/if}
      </div>

      {#if value}
        <div class="mt-2 space-y-1.5 pl-1 sm:pl-28">
          {#each value.breaks as window, index (index)}
            <!-- Label, two fields and the remove control fit one line at 390 px only without a
                 fixed label width and with a tighter gap; wrapping the ✕ onto its own row reads
                 as a stray glyph. -->
            <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <span class="shrink-0 text-xs text-text-muted">
                {t("leave.schedule.break")}
              </span>
              <div class="w-24 shrink-0">
                <TimeInput
                  id="{name}-{day}-break-{index}-start"
                  name="{name}-{day}-break-{index}-start"
                  bind:value={window.start}
                />
              </div>
              <span class="text-text-muted">–</span>
              <div class="w-24 shrink-0">
                <TimeInput
                  id="{name}-{day}-break-{index}-end"
                  name="{name}-{day}-break-{index}-end"
                  bind:value={window.end}
                />
              </div>
              <button
                type="button"
                {disabled}
                class="rounded p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                aria-label={t("leave.schedule.remove_break")}
                onclick={() => removeBreak(day, index)}
              >
                <X size={14} />
              </button>
            </div>
          {/each}
          <button
            type="button"
            {disabled}
            class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
            onclick={() => addBreak(day)}
          >
            <Plus size={12} />
            {t("leave.schedule.add_break")}
          </button>
        </div>
      {/if}

      {#if problem}
        <p class="mt-1.5 text-xs text-red-600 dark:text-red-400 sm:pl-28">{t(problem)}</p>
      {/if}
    </div>
  {/each}
</div>

<div class="mt-3 flex items-center justify-between">
  <p class="text-sm font-medium text-text">
    {t("leave.schedule.week_total", { hours: fmtHours(total) })}
  </p>
  {#if error}
    <p class="text-xs text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}
</div>
