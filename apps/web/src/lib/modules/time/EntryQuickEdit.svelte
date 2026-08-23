<script lang="ts">
  /**
   * Correct one registration from the record it was booked on (#400) — the client's Uren panel.
   *
   * It is deliberately **not** `EntryForm`. That form's job is deciding what an entry is *about*:
   * client, project, task, each a type-ahead over a lookup the host page has to load. On a
   * client's own hub the client is a given, and loading the org's projects, tasks and status
   * vocabulary on every page open — for a dialog most opens never reach — is the shape
   * docs/PERFORMANCE.md rejects, and the one #400 states outright ("the extra data costs no extra
   * query"). What is wanted here is the correction somebody makes while the client is on the
   * phone: the day, the clock, the duration, the words, and whether we bill for it.
   *
   * So it posts **only the fields it draws**. The host's `updateEntry` reads them with
   * `form.has()` and the API updates with `exclude_unset`, so nothing this dialog does not show —
   * the project, the task, the entry type — is cleared by saving it (CLAUDE.md §18: absent means
   * leave alone). A wholesale write from a partial form is exactly how a permission-hidden block
   * gets wiped by a restricted caller's ordinary save.
   *
   * **Host contract:** the page must expose `?/updateEntry`, reading only the posted fields.
   */
  import { enhance } from "$app/forms";

  import { formatDurationInput, parseDurationText } from "$lib/core/duration";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import { endFromDuration, minutesBetween } from "./duration";
  import { formatMinutes } from "./format";

  interface Entry {
    id: string;
    started_at: string;
    ended_at?: string | null;
    break_minutes?: number | null;
    minutes: number;
    billable?: boolean;
    description?: string | null;
  }

  let {
    entry,
    action = "?/updateEntry",
    oncancel,
    ondone,
  }: {
    entry: Entry;
    action?: string;
    oncancel?: () => void;
    /** Called on a **successful** save only — a refusal keeps the dialog open over its reason. */
    ondone?: () => void;
  } = $props();

  // Entry times are stored as the wall-clock the user typed (as UTC), so the ISO string is sliced
  // rather than parsed — `format.ts` states the rule and `EntryForm` reads it the same way.
  //
  // Every seed below is a **deliberate** initial capture, as in `EntryForm`: these are the fields
  // being typed into, so following the prop afterwards would overwrite what the user is editing.
  // The host mounts this inside `{#key entry.id}`, which is what makes a different row a
  // different component rather than the same one with new values.
  // svelte-ignore state_referenced_locally
  let fDate = $state(entry.started_at.slice(0, 10));
  // svelte-ignore state_referenced_locally
  let fStart = $state(entry.started_at.slice(11, 16));
  // svelte-ignore state_referenced_locally
  let fEnd = $state(entry.ended_at ? entry.ended_at.slice(11, 16) : "");
  // svelte-ignore state_referenced_locally
  let fBreak = $state<number | null>(entry.break_minutes ?? 0);
  // svelte-ignore state_referenced_locally
  let fBillable = $state(entry.billable ?? true);
  // svelte-ignore state_referenced_locally
  let fDescription = $state(entry.description ?? "");
  // svelte-ignore state_referenced_locally
  let durationText = $state(formatDurationInput(entry.minutes));

  const workedMinutes = $derived.by(() => {
    const span = minutesBetween(fStart, fEnd);
    if (span == null) return null;
    return Math.max(0, span - Math.max(0, fBreak ?? 0));
  });

  /** Times moved: the duration follows them. */
  function syncDurationFromTimes() {
    durationText = workedMinutes != null ? formatDurationInput(workedMinutes) : "";
  }

  /** A duration was typed: the end time is back-computed from it, as in the full form. */
  function syncEndFromDuration() {
    const minutes = parseDurationText(durationText);
    if (minutes == null || minutes <= 0) return;
    const end = endFromDuration(fStart, minutes, fBreak ?? 0);
    if (end) fEnd = end;
    durationText = formatDurationInput(minutes);
  }

  const busy = new InFlight();
  /**
   * The refusal this form earned, kept here rather than read off `page.form`: the client hub has
   * a dozen actions on it, and a shared `form` store would show this dialog somebody else's
   * error. The API refuses a real case — an entry a manager has signed off is locked to whoever
   * may approve it — so the dialog stays open over the reason instead of closing on it.
   */
  let submitError = $state<string | null>(null);
  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<form
  method="POST"
  {action}
  class="space-y-3"
  use:enhance={busy.wrap("", () => async ({ result, update }) => {
    submitError =
      result.type === "failure"
        ? ((result.data?.error as string | undefined) ?? "errors.validation")
        : null;
    if (result.type === "success") ondone?.();
    // An edit form is never reset: `HTMLFormElement.reset()` would rewind every bound control
    // to a `defaultValue` a Svelte-managed input does not have (docs/UX.md).
    await update({ reset: false });
  })}
>
  <input type="hidden" name="id" value={entry.id} />

  <div>
    <label for="qe-date-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
      {t("time.field.date")}
    </label>
    <DateInput id="qe-date-{entry.id}" name="date" bind:value={fDate} required />
  </div>

  <div class="grid grid-cols-3 gap-2">
    <div>
      <label for="qe-start-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
        {t("time.field.start")}
      </label>
      <TimeInput
        id="qe-start-{entry.id}"
        name="start"
        required
        bind:value={fStart}
        onchange={syncDurationFromTimes}
      />
    </div>
    <div>
      <label for="qe-end-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
        {t("time.field.end")}
      </label>
      <TimeInput
        id="qe-end-{entry.id}"
        name="end"
        required
        bind:value={fEnd}
        onchange={syncDurationFromTimes}
      />
    </div>
    <div>
      <label for="qe-break-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
        {t("time.field.break")}
      </label>
      <DurationInput
        id="qe-break-{entry.id}"
        name="break_minutes"
        bind:minutes={fBreak}
        onchange={syncDurationFromTimes}
        placeholder="0:30"
      />
    </div>
  </div>

  <div class="flex items-center gap-3">
    <div class="flex-1">
      <label for="qe-duration-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
        {t("time.field.duration")}
      </label>
      <input
        id="qe-duration-{entry.id}"
        bind:value={durationText}
        onchange={syncEndFromDuration}
        placeholder={t("common.duration_hint")}
        class={inputClass}
      />
    </div>
    <div
      class="pt-5 text-sm font-semibold tabular-nums {workedMinutes
        ? 'text-brand'
        : 'text-text-muted'}"
    >
      {workedMinutes != null ? t("time.worked", { duration: formatMinutes(workedMinutes) }) : "—"}
    </div>
  </div>

  <input type="hidden" name="billable" value={fBillable} />
  <div class="grid grid-cols-2 gap-2">
    <button
      type="button"
      onclick={() => (fBillable = false)}
      class="rounded-lg border px-3 py-2 text-sm font-medium {!fBillable
        ? 'border-brand bg-brand text-white'
        : 'border-border text-text-muted'}"
    >
      {t("time.not_billable")}
    </button>
    <button
      type="button"
      onclick={() => (fBillable = true)}
      class="rounded-lg border px-3 py-2 text-sm font-medium {fBillable
        ? 'border-brand bg-brand text-white'
        : 'border-border text-text-muted'}"
    >
      {t("time.billable")}
    </button>
  </div>

  <div>
    <label for="qe-description-{entry.id}" class="mb-1 block text-xs font-medium text-text-muted">
      {t("time.field.description")}
    </label>
    <textarea
      id="qe-description-{entry.id}"
      name="description"
      rows="2"
      class={inputClass}
      bind:value={fDescription}></textarea>
  </div>

  <!-- What this dialog cannot change, said out loud rather than left to be discovered: the
       project and the task stay whatever they were, and are edited where they are picked. -->
  <p class="text-xs text-text-muted">{t("time.quick_edit.scope_note")}</p>

  {#if submitError}<p class="text-sm text-red-600">{t(submitError)}</p>{/if}

  <div class="flex gap-2">
    <Button type="submit" loading={busy.active} class="flex-1">{t("common.save")}</Button>
    {#if oncancel}
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={oncancel}
      >
        {t("common.cancel")}
      </button>
    {/if}
  </div>
</form>
