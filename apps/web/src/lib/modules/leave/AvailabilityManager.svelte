<script lang="ts">
  /**
   * A freelancer's availability: the exceptions on top of the week they were engaged under.
   *
   * One surface, shared by the freelancer's own page (`/leave`) and the manager's roster ⋯ menu,
   * so the two cannot drift (docs/UX.md). Whose week it writes is the `userId` prop — omitted
   * for "me", which is what the API resolves an absent `user_id` to; anybody else's needs
   * `leave.availability.write:any` and the API re-checks it.
   *
   * **Three acts, not one form with a mode.** Adding a day you *will* work, dropping one you
   * will not, and swapping one for another are three different things a person says, and a
   * single form with a kind dropdown would make the swap — the commonest of the three — read as
   * two unrelated edits. The move posts to its own action and comes back as one paired row.
   *
   * The contract is deliberately not editable here: it is the agency's record of what was
   * agreed, and "I'm also free on Wednesdays from now on" is a weekly extra, not a rewrite of
   * the period somebody was engaged under.
   *
   * Posts to `?/saveAvailability`, `?/moveAvailability` and `?/deleteAvailability` — every host
   * declares them by spreading `availabilityActions` (availability.server.ts).
   */
  import { ArrowRight, CalendarPlus, CalendarX, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtClockTime, fmtNumericDate, RANGE_DASH } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  export interface AvailabilityEntry {
    id: string;
    user_id: string;
    kind: string;
    date: string;
    start_time?: string | null;
    end_time?: string | null;
    repeat_weeks?: number | null;
    repeat_until?: string | null;
    /** Shared by the two halves of a move; `null` on a standalone row. */
    pair_id?: string | null;
    note?: string | null;
  }

  let {
    entries = [],
    userId = "",
    error = null,
    ondone,
  }: {
    /** This person's exception rows, already narrowed to the read window. */
    entries?: AvailabilityEntry[];
    /** Whose week a save writes; `""` = the signed-in user (the API's own default). */
    userId?: string;
    error?: string | null;
    /** A row landed: the host may close its modal and own the confirmation (#271). */
    ondone?: () => void;
  } = $props();

  const busy = new InFlight();

  type Tab = "extra" | "unavailable" | "move";
  let tab = $state<Tab>("extra");

  // One shared set of fields across the three tabs: they ask the same questions, and resetting
  // them per tab would throw away a date the user has already typed while deciding.
  let day = $state("");
  let toDay = $state("");
  let partDay = $state(false);
  let startTime = $state("");
  let endTime = $state("");
  let repeatWeeks = $state("");
  let repeatUntil = $state("");
  let note = $state("");

  let deleteId = $state("");
  let deleteOpen = $state(false);

  const ready = $derived(tab === "move" ? Boolean(day && toDay) : Boolean(day));

  /** The two halves of a move render as one line; the `extra` half carries the times. */
  const rows = $derived.by(() => {
    const byPair: Record<string, AvailabilityEntry[]> = {};
    const singles: AvailabilityEntry[] = [];
    for (const entry of entries) {
      if (entry.pair_id) (byPair[entry.pair_id] ??= []).push(entry);
      else singles.push(entry);
    }
    const moves = Object.values(byPair)
      .map((group) => ({
        kind: "move" as const,
        from: group.find((e) => e.kind === "unavailable") ?? group[0],
        to: group.find((e) => e.kind === "extra") ?? group[0],
      }))
      // A pair whose halves were somehow separated is not a move any more; the surviving row
      // is shown on its own rather than as half a swap nobody made.
      .filter((m) => m.from !== m.to);
    const paired = new Set(moves.flatMap((m) => [m.from.id, m.to.id]));
    const orphans = entries.filter((e) => e.pair_id && !paired.has(e.id));
    return [
      ...moves.map((m) => ({ ...m, sortKey: m.from.date })),
      ...[...singles, ...orphans].map((entry) => ({
        kind: "single" as const,
        entry,
        sortKey: entry.date,
      })),
    ].sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  });

  function windowText(entry: AvailabilityEntry): string {
    if (!entry.start_time && !entry.end_time) return t("leave.availability.whole_day");
    return `${entry.start_time ? fmtClockTime(entry.start_time) : ""}${RANGE_DASH}${
      entry.end_time ? fmtClockTime(entry.end_time) : ""
    }`;
  }

  function repeatText(entry: AvailabilityEntry): string | null {
    if (!entry.repeat_weeks) return null;
    const cadence =
      entry.repeat_weeks === 1
        ? t("leave.recurring.every_week")
        : t("leave.recurring.every_n", { n: entry.repeat_weeks });
    return entry.repeat_until
      ? `${cadence} ${t("leave.availability.until", { date: fmtNumericDate(entry.repeat_until) })}`
      : cadence;
  }

  const tabClass = (active: boolean) =>
    `flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm ${
      active ? "border-brand bg-brand/10 font-medium text-brand" : "border-border text-text-muted"
    }`;
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-4">
  {#if rows.length > 0}
    <ul class="divide-y divide-border rounded-lg border border-border text-sm">
      {#each rows as row (row.kind === "move" ? row.from.id : row.entry.id)}
        {@const primary = row.kind === "move" ? row.to : row.entry}
        <li class="flex items-start gap-3 px-3 py-2">
          <div class="min-w-0 flex-1">
            {#if row.kind === "move"}
              <span class="flex flex-wrap items-center gap-1.5 text-text">
                <span class="line-through decoration-text-muted">
                  {fmtNumericDate(row.from.date)}
                </span>
                <ArrowRight size={14} class="text-text-muted" />
                <span class="font-medium">{fmtNumericDate(row.to.date)}</span>
              </span>
            {:else}
              <span class="flex items-center gap-1.5 font-medium text-text">
                {#if primary.kind === "extra"}
                  <CalendarPlus size={14} />
                {:else}
                  <CalendarX size={14} />
                {/if}
                {fmtNumericDate(primary.date)}
              </span>
            {/if}
            <span class="mt-0.5 block text-xs text-text-muted">
              {row.kind === "move"
                ? t("leave.availability.moved")
                : primary.kind === "extra"
                  ? t("leave.availability.extra")
                  : t("leave.availability.unavailable")}
              · {windowText(primary)}
              {#if repeatText(primary)}· {repeatText(primary)}{/if}
              {#if primary.note}· {primary.note}{/if}
            </span>
          </div>
          <button
            type="button"
            class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
            title={t("common.delete")}
            aria-label={t("common.delete")}
            onclick={() => {
              deleteId = primary.id;
              deleteOpen = true;
            }}
          >
            <Trash2 size={14} />
          </button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
      {t("leave.availability.empty")}
    </p>
  {/if}

  <div class="flex gap-2 border-t border-border pt-4">
    <button type="button" class={tabClass(tab === "extra")} onclick={() => (tab = "extra")}>
      <CalendarPlus size={14} />
      {t("leave.availability.tab_extra")}
    </button>
    <button
      type="button"
      class={tabClass(tab === "unavailable")}
      onclick={() => (tab = "unavailable")}
    >
      <CalendarX size={14} />
      {t("leave.availability.tab_unavailable")}
    </button>
    <button type="button" class={tabClass(tab === "move")} onclick={() => (tab = "move")}>
      <ArrowRight size={14} />
      {t("leave.availability.tab_move")}
    </button>
  </div>

  <form
    id="availability-form"
    method="POST"
    action={tab === "move" ? "?/moveAvailability" : "?/saveAvailability"}
    class="space-y-3"
    use:enhance={busy.wrap("save", () => ({ result, update }) => {
      // A row is a completed act, not a form to sit in: clear the fields and let the host
      // decide whether to close (docs/UX.md — every enhanced form states its reset).
      if (result.type === "success") {
        day = toDay = startTime = endTime = repeatWeeks = repeatUntil = note = "";
        partDay = false;
        ondone?.();
      }
      void update({ reset: false });
    })}
  >
    {#if userId}<input type="hidden" name="user_id" value={userId} />{/if}
    {#if tab !== "move"}<input type="hidden" name="kind" value={tab} />{/if}

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div>
        <label for="a-date" class="mb-1 block text-xs text-text-muted">
          {tab === "move" ? t("leave.availability.from_day") : t("leave.availability.day")}
        </label>
        <DateInput
          id="a-date"
          name={tab === "move" ? "from_date" : "date"}
          formId="availability-form"
          bind:value={day}
        />
      </div>
      {#if tab === "move"}
        <div>
          <label for="a-to" class="mb-1 block text-xs text-text-muted">
            {t("leave.availability.to_day")}
          </label>
          <DateInput id="a-to" name="to_date" formId="availability-form" bind:value={toDay} />
        </div>
      {/if}
    </div>

    <div>
      <button
        type="button"
        class="text-xs {partDay ? 'text-brand' : 'text-text-muted hover:text-brand'}"
        onclick={() => (partDay = !partDay)}
      >
        {partDay ? t("leave.form.whole_days") : t("leave.form.part_day")}
      </button>
      {#if partDay}
        <div class="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label for="a-start" class="mb-1 block text-xs text-text-muted">
              {t("leave.form.start_time")}
            </label>
            <TimeInput
              id="a-start"
              name="start_time"
              formId="availability-form"
              bind:value={startTime}
            />
          </div>
          <div>
            <label for="a-end" class="mb-1 block text-xs text-text-muted">
              {t("leave.form.end_time")}
            </label>
            <TimeInput id="a-end" name="end_time" formId="availability-form" bind:value={endTime} />
          </div>
        </div>
        <p class="mt-1 text-xs text-text-muted">
          {tab === "move"
            ? t("leave.availability.move_times_hint")
            : t("leave.availability.times_hint")}
        </p>
      {/if}
    </div>

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div>
        <label for="a-repeat" class="mb-1 block text-xs text-text-muted">
          {t("leave.availability.repeat")}
        </label>
        <select id="a-repeat" name="repeat_weeks" bind:value={repeatWeeks} class={inputClass}>
          <option value="">{t("leave.availability.repeat_none")}</option>
          {#each [1, 2, 3, 4, 6, 8] as weeks (weeks)}
            <option value={String(weeks)}>
              {weeks === 1
                ? t("leave.recurring.every_week")
                : t("leave.recurring.every_n", { n: weeks })}
            </option>
          {/each}
        </select>
      </div>
      {#if repeatWeeks}
        <div>
          <label for="a-until" class="mb-1 block text-xs text-text-muted">
            {t("leave.availability.repeat_until")}
          </label>
          <DateInput
            id="a-until"
            name="repeat_until"
            formId="availability-form"
            bind:value={repeatUntil}
          />
          <p class="mt-1 text-xs text-text-muted">{t("leave.availability.repeat_until_hint")}</p>
        </div>
      {/if}
    </div>

    <div>
      <label for="a-note" class="mb-1 block text-xs text-text-muted">{t("leave.form.note")}</label>
      <input id="a-note" name="note" bind:value={note} class={inputClass} />
    </div>

    {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}

    <div class="flex justify-end">
      <Button loading={busy.is("save")} disabled={!ready}>{t("common.add")}</Button>
    </div>
  </form>
</div>

<!-- Deleting one half of a move removes both — the API owns that rule, and the confirmation
     says so rather than leaving somebody unavailable on a day they had agreed to swap. -->
<ConfirmDialog
  bind:open={deleteOpen}
  title={t("common.delete")}
  message={t("leave.availability.delete_confirm")}
  action="?/deleteAvailability"
  fields={{ id: deleteId }}
  confirmLabel={t("common.delete")}
/>
