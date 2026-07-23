<script lang="ts">
  /**
   * One surface for recurring rostered free days (#107), shared by the manager's modal
   * (Instellingen → Gebruikers) and the employee's own on /leave — one form, one row shape,
   * so the two can't drift (docs/UX.md). The caller decides whose patterns and which types:
   * managers pass every active type, the self-service surface passes only auto-approve ones
   * (the API enforces the same split — generated days are pre-approved, so a member pattern
   * for an approval-requiring type would bypass the approval flow).
   *
   * Posts to `?/saveRecurring`, `?/toggleRecurring` and `?/deleteRecurring` — both host pages
   * declare all three.
   */
  import { Clock, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtClockTime, fmtNumericDate, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { getLocale } from "$lib/paraglide/runtime";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import { typeLabel, type LeaveTypeInfo } from "./format";

  interface RecurringPattern {
    id: string;
    user_id: string;
    leave_type_id: string;
    anchor_date: string;
    interval_weeks: number;
    /** Optional in the generated client (the API schema carries defaults). */
    start_time?: string | null;
    end_time?: string | null;
    active: boolean;
  }

  let {
    patterns,
    types,
    userId,
    error = null,
    generated = null,
  }: {
    /** This user's patterns. */
    patterns: RecurringPattern[];
    /** The types the caller may plan with (already filtered per surface). */
    types: LeaveTypeInfo[];
    /** Whose pattern a save creates (hidden field; the API re-checks ownership). */
    userId: string;
    error?: string | null;
    /** How many days the last save placed — shown as the success line. */
    generated?: number | null;
  } = $props();

  const locale = getLocale();
  const typeById = $derived(Object.fromEntries(types.map((lt) => [lt.id, lt])));
  const defaultTypeId = $derived(
    (types.find((lt) => lt.key === "roostervrij") ?? types[0])?.id ?? "",
  );

  // Part-day patterns ("off from 15:00"): the times sit behind the same toggle the request
  // form uses — most patterns are whole days, and two more required fields would say otherwise.
  let partDay = $state(false);
  let startTime = $state("");
  let endTime = $state("");
  let deleteId = $state("");
  let deleteOpen = $state(false);

  const busy = new InFlight();

  function intervalText(weeks: number): string {
    return weeks === 1
      ? t("leave.recurring.every_week")
      : t("leave.recurring.every_n", { n: weeks });
  }

  function windowText(pattern: RecurringPattern): string | null {
    if (pattern.start_time && pattern.end_time) {
      return `${fmtClockTime(pattern.start_time)} – ${fmtClockTime(pattern.end_time)}`;
    }
    if (pattern.start_time)
      return t("leave.recurring.from_time", { time: fmtClockTime(pattern.start_time) });
    if (pattern.end_time)
      return t("leave.recurring.until_time", { time: fmtClockTime(pattern.end_time) });
    return null;
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-4">
  {#if patterns.length > 0}
    <ul class="divide-y divide-border rounded-lg border border-border">
      {#each patterns as pattern (pattern.id)}
        <li class="flex items-center gap-3 px-3 py-2 text-sm">
          <div class="min-w-0 flex-1">
            <span class="font-medium capitalize text-text">
              {fmtWeekdayShort(pattern.anchor_date)}
              <span class="font-normal normal-case text-text-muted">
                · {intervalText(pattern.interval_weeks)}
                {#if windowText(pattern)}
                  · {windowText(pattern)}
                {/if}
                · {t("leave.recurring.since", { date: fmtNumericDate(pattern.anchor_date) })}
              </span>
            </span>
            <span class="block text-xs text-text-muted">
              {typeLabel(typeById[pattern.leave_type_id], locale)}
              {#if !pattern.active}
                · {t("leave.recurring.inactive")}
              {/if}
            </span>
          </div>
          <form method="POST" action="?/toggleRecurring" use:enhance={busy.wrap(pattern.id)}>
            <input type="hidden" name="id" value={pattern.id} />
            <input type="hidden" name="active" value={String(!pattern.active)} />
            <Button
              variant="secondary"
              size="xs"
              loading={busy.is(pattern.id)}
              disabled={busy.active}
            >
              {pattern.active ? t("settings.leave.deactivate") : t("settings.leave.activate")}
            </Button>
          </form>
          <button
            type="button"
            class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
            title={t("common.delete")}
            aria-label={t("common.delete")}
            onclick={() => {
              deleteId = pattern.id;
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
      {t("leave.recurring.empty")}
    </p>
  {/if}

  {#if generated !== null}
    <p class="text-sm text-green-600">
      {t("leave.recurring.generated", { count: generated })}
    </p>
  {/if}

  {#key patterns.length}
    <form
      method="POST"
      action="?/saveRecurring"
      class="space-y-3 border-t border-border pt-4"
      use:enhance={busy.wrap("save", () => ({ result, update }) => {
        if (result.type === "success") void update({ reset: true });
        else void update({ reset: false });
      })}
    >
      <input type="hidden" name="user_id" value={userId} />
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("leave.recurring.add")}
      </p>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <label for="r-type" class="mb-1 block text-xs text-text-muted"
            >{t("leave.recurring.type")}</label
          >
          <select id="r-type" name="leave_type_id" required class={inputClass}>
            {#each types as lt (lt.id)}
              <option value={lt.id} selected={lt.id === defaultTypeId}>
                {typeLabel(lt, locale)}
              </option>
            {/each}
          </select>
        </div>
        <div>
          <label for="r-anchor" class="mb-1 block text-xs text-text-muted"
            >{t("leave.recurring.first_day")}</label
          >
          <DateInput id="r-anchor" name="anchor_date" required />
        </div>
        <div>
          <label for="r-interval" class="mb-1 block text-xs text-text-muted"
            >{t("leave.recurring.interval")}</label
          >
          <select id="r-interval" name="interval_weeks" class={inputClass}>
            {#each [1, 2, 3, 4] as weeks (weeks)}
              <option value={weeks} selected={weeks === 2}>{intervalText(weeks)}</option>
            {/each}
          </select>
        </div>
      </div>

      <!-- Most patterns are whole days: the window is an affordance, not two more fields. -->
      <div>
        <button
          type="button"
          class="flex items-center gap-1.5 text-xs {partDay
            ? 'text-brand'
            : 'text-text-muted hover:text-brand'}"
          onclick={() => (partDay = !partDay)}
        >
          <Clock size={13} />
          {partDay ? t("leave.form.whole_days") : t("leave.form.part_day")}
        </button>
        {#if partDay}
          <div class="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label for="r-start-time" class="mb-1 block text-xs text-text-muted"
                >{t("leave.form.start_time")}</label
              >
              <TimeInput id="r-start-time" name="start_time" bind:value={startTime} />
            </div>
            <div>
              <label for="r-end-time" class="mb-1 block text-xs text-text-muted"
                >{t("leave.form.end_time")}</label
              >
              <TimeInput id="r-end-time" name="end_time" bind:value={endTime} />
            </div>
          </div>
          <p class="mt-1 text-xs text-text-muted">{t("leave.form.times_hint")}</p>
        {/if}
      </div>

      <p class="text-xs text-text-muted">{t("leave.recurring.hint")}</p>
      {#if error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
      {/if}
      <div class="flex justify-end">
        <Button loading={busy.is("save")} disabled={busy.active}>
          {t("leave.recurring.add")}
        </Button>
      </div>
    </form>
  {/key}
</div>

<ConfirmDialog
  bind:open={deleteOpen}
  title={t("common.delete")}
  message={t("leave.recurring.delete_confirm")}
  action="?/deleteRecurring"
  fields={{ id: deleteId }}
  confirmLabel={t("common.delete")}
/>
