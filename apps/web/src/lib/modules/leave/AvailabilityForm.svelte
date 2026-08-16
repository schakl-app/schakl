<script lang="ts">
  /**
   * Writing one availability row — creating it, or correcting the one you already wrote.
   *
   * **Three acts on create, one on edit.** Adding a day you *will* work, dropping one you will
   * not, and swapping one for another are three different things a person says, and a single
   * form with a kind dropdown would make the swap — the commonest of the three — read as two
   * unrelated edits. Editing has no such split: the move already exists as two rows, and
   * correcting the replacement day's hours is not a statement about the day that was dropped, so
   * an edit is always one row with its kind on a toggle.
   *
   * Posts to `?/saveAvailability`, `?/moveAvailability` or `?/updateAvailability` — every host
   * declares all three by spreading `availabilityActions` (availability.server.ts).
   */
  import { ArrowRight, CalendarPlus, CalendarX } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { type PickerMember } from "$lib/core/members";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import MemberPicker from "$lib/core/ui/MemberPicker.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import type { AvailabilityEntry } from "./availability";

  let {
    entry = null,
    defaultDate = "",
    userId = "",
    people = [],
    error = null,
    ondone,
  }: {
    /** The row being corrected; `null` creates a new one. */
    entry?: AvailabilityEntry | null;
    /** The day a create opens on — the agenda's own, so the date already on screen is not
     *  retyped. Ignored on an edit, which opens on the row's own day. */
    defaultDate?: string;
    /** Whose week a create writes; `""` = the signed-in user (the API's own default). */
    userId?: string;
    /**
     * Colleagues this viewer may write for (`leave.availability.write:any`) — when non-empty the
     * form asks *whose* week rather than assuming, which is what a cross-person overview needs.
     * The shared `MemberPicker`, never a native select and never a flat list of everybody ever
     * hired (docs/UX.md): booking a day onto a deactivated freelancer's week is exactly the
     * quiet failure that split exists to prevent, and the page's own filter above it had the
     * rule while the form that writes did not. Empty on the per-person surfaces, where the host
     * already answered the question by opening that person's modal.
     */
    people?: PickerMember[];
    error?: string | null;
    /** A row landed: the host may close its modal and own the confirmation (#271). */
    ondone?: () => void;
  } = $props();

  const busy = new InFlight();
  const editing = $derived(entry !== null);

  type Tab = "extra" | "unavailable" | "move";
  // On an edit the tab *is* the row's kind, so the toggle above the fields is the same control
  // in both modes rather than a second way of saying the same thing.
  let tab = $state<Tab>(entry ? ((entry.kind as Tab) ?? "extra") : "extra");

  // One shared set of fields across the three tabs: they ask the same questions, and resetting
  // them per tab would throw away a date the user has already typed while deciding.
  let day = $state(entry?.date ?? defaultDate);
  let toDay = $state("");
  let partDay = $state(Boolean(entry?.start_time || entry?.end_time));
  let startTime = $state(entry?.start_time ?? "");
  let endTime = $state(entry?.end_time ?? "");
  let repeatWeeks = $state(entry?.repeat_weeks ? String(entry.repeat_weeks) : "");
  let repeatUntil = $state(entry?.repeat_until ?? "");
  let note = $state(entry?.note ?? "");
  let person = $state(userId);

  // A picked person is a *create*-time question only: an edit acts on a row that already belongs
  // to somebody, and moving it to another person is not a correction, it is two acts.
  const picking = $derived(!editing && people.length > 0);
  const ready = $derived(
    (picking ? Boolean(person) : true) && (tab === "move" ? Boolean(day && toDay) : Boolean(day)),
  );
  const action = $derived(
    editing ? "?/updateAvailability" : tab === "move" ? "?/moveAvailability" : "?/saveAvailability",
  );

  // Unticking "part of the day" must actually clear the times, or an edit posts a window the
  // user just said they did not want — the fields are hidden, and hidden is not empty.
  function togglePartDay(): void {
    partDay = !partDay;
    if (!partDay) startTime = endTime = "";
  }

  const tabClass = (active: boolean) =>
    `flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm ${
      active ? "border-brand bg-brand/10 font-medium text-brand" : "border-border text-text-muted"
    }`;
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-4">
  <div class="flex gap-2">
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
    <!-- No swap on an edit: the pair already exists, and turning one row into a move would
         leave the day it used to drop unaccounted for. -->
    {#if !editing}
      <button type="button" class={tabClass(tab === "move")} onclick={() => (tab = "move")}>
        <ArrowRight size={14} />
        {t("leave.availability.tab_move")}
      </button>
    {/if}
  </div>

  <form
    id="availability-form"
    method="POST"
    {action}
    class="space-y-3"
    use:enhance={busy.wrap("save", () => ({ result, update }) => {
      // A created row is a completed act, not a form to sit in, so the fields clear; an edit
      // keeps what it just saved, because the host closes over it (docs/UX.md — every enhanced
      // form states its reset).
      if (result.type === "success") {
        if (!editing) {
          day = toDay = startTime = endTime = repeatWeeks = repeatUntil = note = "";
          partDay = false;
        }
        ondone?.();
      }
      void update({ reset: false });
    })}
  >
    {#if editing}<input type="hidden" name="id" value={entry?.id} />{/if}
    {#if !editing && !picking && userId}
      <input type="hidden" name="user_id" value={userId} />
    {/if}
    {#if tab !== "move"}<input type="hidden" name="kind" value={tab} />{/if}

    {#if picking}
      <div>
        <label for="a-person" class="mb-1 block text-xs text-text-muted">
          {t("leave.team.member")}
        </label>
        <MemberPicker
          id="a-person"
          name="user_id"
          formId="availability-form"
          bind:value={person}
          members={people}
          placeholder={t("leave.availability.person_placeholder")}
        />
      </div>
    {/if}

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
        onclick={togglePartDay}
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
      <Button loading={busy.is("save")} disabled={!ready}>
        {editing ? t("common.save") : t("common.add")}
      </Button>
    </div>
  </form>
</div>
