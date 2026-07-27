<script lang="ts">
  /**
   * One employee's whole employment arrangement, in three steps: **contract → werkweek → vrije
   * tijd**.
   *
   * It replaces three separate ⋯ modals (Werkrooster, Contracten, Terugkerende vrije tijd), and
   * the merge is the point rather than tidying. Those three were never three decisions: contract
   * hours only mean something measured against the week that is actually worked, and free days
   * exist *because* the two differ. Split across three surfaces, the relationship was invisible —
   * a 36-hour contract quietly grew a pot of free days nobody had asked about, and a part-timer
   * on a four-day roster grew one twice over. Here every number is derived in front of you as you
   * type it, and the one modelling choice the system cannot infer is **asked** (step 2) instead of
   * guessed from whatever schedule an admin happened to enter.
   *
   * One save (docs/UX.md: one save button per editing surface) posting to `?/saveEmployment`,
   * which writes the contract, its week and its free-time rule together, then places the pattern.
   * The result screen reports what landed and, when a changed contract left free days the pot no
   * longer covers, offers to give them back — never silently cancelling somebody's plans.
   */
  import { ArrowLeft, ArrowRight, Check, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { getLocale } from "$lib/paraglide/runtime";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import { fmtHours, typeLabel, type LeaveTypeInfo } from "./format";
  import RecurringDeleteDialog from "./RecurringDeleteDialog.svelte";
  import { averageDayHours, cloneSchedule, weekHours, type WorkSchedule } from "./schedule";
  import WorkScheduleEditor from "./WorkScheduleEditor.svelte";

  export interface WizardContract {
    id: string;
    user_id: string;
    contract_hours_per_week: string | number;
    scheduled_hours_per_week: string | number;
    free_time_hours_per_week?: string | number | null;
    effective_free_time_per_week?: string | number | null;
    schedule?: unknown;
    start_date: string;
    end_date: string | null;
  }
  export interface WizardPattern {
    id: string;
    user_id: string;
    leave_type_id: string;
    anchor_date: string;
    interval_weeks: number;
    days_per_year?: number | null;
    start_time?: string | null;
    end_time?: string | null;
    active: boolean;
    /** Days this pattern still has standing from today on — what a delete puts at stake. */
    upcoming_days?: number;
  }
  /** The `?/saveEmployment` result the wizard reports on. */
  export interface WizardResult {
    employmentSaved?: boolean;
    employmentGenerated?: number;
    withdrawn?: number;
    /** A pattern delete landed; `withdrawn` then counts the days it took back. */
    patternDeleted?: boolean;
    error?: string | null;
    freeTime?: {
      entitled_hours: string | number;
      placed_hours: string | number;
      unplaced_hours: string | number;
      overhang_hours: string | number;
      hours_per_day: string | number;
      next_date: string | null;
      overhang: { request_id: string; date: string; hours: string | number }[];
    } | null;
  }

  let {
    memberName,
    userId,
    contracts = [],
    patterns = [],
    leaveTypes = [],
    orgDefaultSchedule,
    form = null,
    onterminate,
  }: {
    memberName: string;
    userId: string;
    /** This member's contracts, oldest first. */
    contracts?: WizardContract[];
    patterns?: WizardPattern[];
    leaveTypes?: LeaveTypeInfo[];
    orgDefaultSchedule: WorkSchedule;
    form?: WizardResult | null;
    /** Open the terminate dialog for a contract — the host owns it, as before. */
    onterminate?: (contract: WizardContract) => void;
  } = $props();

  const busy = new InFlight();
  const locale = getLocale();
  const todayIso = new Date().toISOString().slice(0, 10);
  // Deleting a contract confirms and posts from its own dialog (docs/UX.md: every delete
  // confirms, and the dialog owns the form) — a submit inside the wizard form would post the
  // wizard instead.
  let deleteId = $state("");
  let deleteOpen = $state(false);
  // Deleting a pattern asks a second question (what happens to the days it placed), so it gets
  // its own dialog rather than the plain ConfirmDialog the contract delete uses.
  let deletePattern = $state<WizardPattern | null>(null);
  let deletePatternOpen = $state(false);

  // --- what exists today ----------------------------------------------------------
  /** The period in force: no end date, or one that has not passed. */
  const openContract = $derived(
    contracts.find((c) => !c.end_date || c.end_date >= todayIso) ?? null,
  );
  /** The same, resolved once at construction, to seed the form state below.
   *
   *  Capturing the initial value is the point, not an oversight: these seed `$state` fields the
   *  user then edits, and a reactive read would overwrite their typing on every prop update. The
   *  host re-mounts this component per member and per completed run, so "initial" is always the
   *  right snapshot. */
  // svelte-ignore state_referenced_locally
  const initial = contracts.find((c) => !c.end_date || c.end_date >= todayIso) ?? null;
  const initialWeek = (initial?.schedule ?? null) as WorkSchedule | null;
  const storedFreeTime = initial?.free_time_hours_per_week;
  const freeTimeType = $derived(
    leaveTypes.find((lt) => lt.key === "roostervrij" && lt.active) ??
      leaveTypes.find((lt) => lt.active && !lt.requires_approval && lt.tracks_balance) ??
      null,
  );

  // --- the flow --------------------------------------------------------------------
  type Mode = "edit" | "new";
  let step = $state(1);
  // Adjusting the arrangement in force is the ordinary act; a *new* period is a raise or a new
  // hire. Defaulting to "new" when there is nothing on file is what stops the wizard opening on
  // an edit form for a contract that does not exist.
  let mode = $state<Mode>(initial ? "edit" : "new");
  let started = $state(false);

  // Step 1 — the new period's own fields (unused while editing).
  let startDate = $state(todayIso);
  let endDate = $state("");
  let contractHours = $state(initial ? String(initial.contract_hours_per_week) : "");

  // Step 2 — the week and the free-time rule.
  let inherit = $state(initialWeek === null);
  // svelte-ignore state_referenced_locally
  let draft = $state<WorkSchedule>(
    cloneSchedule((initialWeek ?? orgDefaultSchedule) as WorkSchedule),
  );
  type FreeTimeMode = "derive" | "roster" | "custom";
  let freeTimeMode = $state<FreeTimeMode>(
    storedFreeTime == null ? "derive" : Number(storedFreeTime) === 0 ? "roster" : "custom",
  );
  let freeTimeCustom = $state(storedFreeTime == null ? "" : String(storedFreeTime));

  // Step 3 — the pattern.
  type PatternMode = "none" | "spread" | "interval";
  let patternMode = $state<PatternMode>("none");
  let anchorDate = $state("");
  let daysPerYear = $state("");
  let intervalWeeks = $state(2);
  let partDay = $state(false);
  let startTime = $state("");
  let endTime = $state("");

  function applyScheduleFrom(contract: WizardContract | null) {
    const own = (contract?.schedule ?? null) as WorkSchedule | null;
    inherit = own === null;
    draft = cloneSchedule((own ?? orgDefaultSchedule) as WorkSchedule);
  }

  function applyFreeTimeFrom(contract: WizardContract | null) {
    const stored = contract?.free_time_hours_per_week;
    if (stored === null || stored === undefined) {
      freeTimeMode = "derive";
      freeTimeCustom = "";
      return;
    }
    freeTimeMode = Number(stored) === 0 ? "roster" : "custom";
    freeTimeCustom = String(stored);
  }

  /** Switching to a new period keeps the current week as the starting point, not a blank grid. */
  function startNewContract() {
    mode = "new";
    startDate = todayIso;
    endDate = "";
    contractHours = "";
    applyScheduleFrom(openContract);
    applyFreeTimeFrom(null);
    step = 1;
  }

  function useCurrentContract() {
    mode = "edit";
    contractHours = openContract ? String(openContract.contract_hours_per_week) : "";
    applyScheduleFrom(openContract);
    applyFreeTimeFrom(openContract);
    step = 1;
  }

  // --- the numbers, derived in front of the user -----------------------------------
  const norm = $derived(weekHours(orgDefaultSchedule));
  const effectiveWeek = $derived(inherit ? orgDefaultSchedule : draft);
  const rosterHours = $derived(weekHours(effectiveWeek));
  const dayHours = $derived(averageDayHours(effectiveWeek));
  const enteredHours = $derived.by(() => {
    const value = Number(String(contractHours).replace(",", ".").trim());
    return Number.isFinite(value) && value > 0 ? value : 0;
  });
  /** Exactly `LeaveService._contract_free_time`, so the preview cannot disagree with the save. */
  const freeTimePerWeek = $derived.by(() => {
    if (freeTimeMode === "roster") return 0;
    if (freeTimeMode === "custom") {
      const value = Number(String(freeTimeCustom).replace(",", ".").trim());
      return Number.isFinite(value) && value > 0 ? value : 0;
    }
    return Math.max(0, norm - enteredHours);
  });
  /** What that pot buys, in days — the number the pattern step is prefilled from. */
  const freeDaysPerYear = $derived(
    dayHours > 0 ? Math.round(((freeTimePerWeek * 52) / dayHours) * 2) / 2 : 0,
  );
  const suggestedInterval = $derived(
    freeDaysPerYear >= 1 ? Math.max(1, Math.min(8, Math.round(52 / freeDaysPerYear))) : 2,
  );

  const step1Ready = $derived(
    mode === "edit" ? Boolean(openContract) : Boolean(startDate) && enteredHours > 0,
  );
  const canPlan = $derived(freeTimeType !== null && freeTimePerWeek > 0);

  function goPattern() {
    // Prefill from the pot the moment the step opens, so the manager confirms a number rather
    // than inventing one. Whole days by default; a window is the deliberate exception.
    if (patternMode === "none" && canPlan) {
      patternMode = "spread";
      daysPerYear = String(Math.max(1, Math.round(freeDaysPerYear)));
      intervalWeeks = suggestedInterval;
    }
    step = 3;
  }

  const result = $derived(form?.employmentSaved ? form : null);
  const overhang = $derived(result?.freeTime?.overhang ?? []);
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const radioRow =
    "flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-sm";
</script>

<div class="space-y-4">
  <p class="text-sm text-text-muted">{memberName}</p>

  {#if result}
    <!-- The save landed; the wizard becomes its own receipt. Whatever the contract change did to
         the calendar is stated here rather than discovered later on the agenda. -->
    <div class="space-y-3">
      <p class="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
        <Check size={16} />
        {t("settings.employment.saved")}
      </p>
      {#if result.freeTime}
        <dl
          class="grid grid-cols-2 gap-3 rounded-lg border border-border p-3 text-sm sm:grid-cols-4"
        >
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.result_entitled")}</dt>
            <dd class="font-medium text-text">
              {t("leave.form.hours_amount", { hours: fmtHours(result.freeTime.entitled_hours) })}
            </dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.result_placed")}</dt>
            <dd class="font-medium text-text">
              <!-- This *year's* placed hours, not the generator's return value. The generator
                   fills the whole 12-month horizon, so it happily reports 26 days next to a
                   92-hour yearly pot and invites the reading that the two divide into each
                   other. The honest total goes on its own line under the grid. -->
              {t("leave.form.hours_amount", { hours: fmtHours(result.freeTime.placed_hours) })}
            </dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.result_unplaced")}</dt>
            <dd class="font-medium text-text">
              {t("leave.form.hours_amount", { hours: fmtHours(result.freeTime.unplaced_hours) })}
            </dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.result_next")}</dt>
            <dd class="font-medium text-text">
              {result.freeTime.next_date ? fmtNumericDate(result.freeTime.next_date) : "—"}
            </dd>
          </div>
        </dl>
      {/if}
      {#if (result.employmentGenerated ?? 0) > 0}
        <!-- Spelled out including the horizon, because the count runs past the current year and
             a bare "26 dagen" beside a yearly balance reads as a contradiction. -->
        <p class="text-sm text-text-muted">
          {t("settings.employment.result_generated", { count: result.employmentGenerated ?? 0 })}
        </p>
      {/if}

      {#if overhang.length > 0}
        <!-- A reprorated pot no longer covers these. Reported with the dates, and withdrawn only
             on this button: cancelling somebody's planned days off is not a side effect. -->
        <div class="rounded-lg border border-amber-300 p-3 dark:border-amber-800">
          <p class="text-sm font-medium text-amber-700 dark:text-amber-400">
            {t("settings.employment.overhang_title", { count: overhang.length })}
          </p>
          <p class="mt-1 text-xs text-text-muted">{t("settings.employment.overhang_hint")}</p>
          <ul class="mt-2 space-y-0.5 text-xs text-text">
            {#each overhang as day (day.request_id)}
              <li>{fmtNumericDate(day.date)} · {fmtHours(day.hours)}</li>
            {/each}
          </ul>
          <form
            method="POST"
            action="?/withdrawFreeTime"
            class="mt-3"
            use:enhance={busy.wrap("withdraw", () => ({ update }) => {
              void update({ reset: false });
            })}
          >
            <input
              type="hidden"
              name="request_ids"
              value={overhang.map((d) => d.request_id).join(",")}
            />
            <Button variant="danger" size="sm" loading={busy.is("withdraw")}>
              {t("settings.employment.overhang_withdraw")}
            </Button>
          </form>
        </div>
      {:else if result.withdrawn !== undefined}
        <p class="text-sm text-green-600 dark:text-green-400">
          {t("settings.employment.overhang_withdrawn", { count: result.withdrawn })}
        </p>
      {/if}
    </div>
  {:else}
    <!-- Step rail. Three numbered dots rather than a progress bar: the steps are named, and the
         user can go back to any one they have already answered. -->
    <ol class="flex items-center gap-2 text-xs">
      {#each [t("settings.employment.step_contract"), t("settings.employment.step_week"), t("settings.employment.step_free_time")] as label, index (label)}
        {@const number = index + 1}
        <li class="flex items-center gap-2">
          <button
            type="button"
            disabled={number > step}
            onclick={() => (step = number)}
            class="flex items-center gap-1.5 rounded-full px-2 py-1 {number === step
              ? 'bg-brand/10 font-medium text-brand'
              : 'text-text-muted'} disabled:cursor-default disabled:opacity-50"
          >
            <span
              class="flex h-5 w-5 items-center justify-center rounded-full text-[11px] {number <=
              step
                ? 'bg-brand text-white'
                : 'bg-surface text-text-muted'}">{number}</span
            >
            {label}
          </button>
          {#if number < 3}<span class="text-text-muted">›</span>{/if}
        </li>
      {/each}
    </ol>

    <!-- Rendered outside the form on purpose (WorkScheduleEditor's TimeInputs post hidden fields
         of their own) and posted into it with `form="…"`. -->
    <div class:hidden={step !== 2}>
      <div class:opacity-50={inherit} class:pointer-events-none={inherit}>
        <WorkScheduleEditor bind:schedule={draft} formId="employment-form" disabled={inherit} />
      </div>
    </div>

    <!-- Step 3's existing patterns, also outside the wizard form: each row carries its own
         activate/deactivate form, and a form cannot be nested in another form. -->
    {#if step === 3 && form?.patternDeleted}
      <p class="text-sm text-green-600 dark:text-green-400">
        {(form.withdrawn ?? 0) > 0
          ? t("leave.recurring.deleted_withdrawn", { count: form.withdrawn ?? 0 })
          : t("leave.recurring.deleted")}
      </p>
    {/if}

    {#if step === 3 && patterns.length > 0}
      <ul class="divide-y divide-border rounded-lg border border-border text-sm">
        {#each patterns as pattern (pattern.id)}
          <li class="flex items-center gap-3 px-3 py-2">
            <div class="min-w-0 flex-1">
              <span class="text-text">
                {pattern.days_per_year
                  ? t("leave.recurring.days_per_year", { count: pattern.days_per_year })
                  : t("leave.recurring.every_n", { n: pattern.interval_weeks })}
              </span>
              <span class="block text-xs text-text-muted">
                {t("leave.recurring.since", { date: fmtNumericDate(pattern.anchor_date) })}
                {#if (pattern.upcoming_days ?? 0) > 0}
                  · {t("leave.recurring.upcoming_days", { count: pattern.upcoming_days ?? 0 })}
                {/if}
                {#if !pattern.active}· {t("leave.recurring.inactive")}{/if}
              </span>
            </div>
            <!-- Deactivate is the non-destructive alternative: it stops future generation and
                 leaves every placed day alone. Offered beside delete so "stop this" does not
                 have to mean "remove it". -->
            <form method="POST" action="?/toggleRecurring" use:enhance={busy.wrap(pattern.id)}>
              <input type="hidden" name="id" value={pattern.id} />
              <input type="hidden" name="active" value={String(!pattern.active)} />
              <Button variant="secondary" size="xs" loading={busy.is(pattern.id)}>
                {pattern.active ? t("settings.leave.deactivate") : t("settings.leave.activate")}
              </Button>
            </form>
            <button
              type="button"
              class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
              title={t("common.delete")}
              aria-label={t("common.delete")}
              onclick={() => {
                deletePattern = pattern;
                deletePatternOpen = true;
              }}
            >
              <Trash2 size={14} />
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    <form
      id="employment-form"
      method="POST"
      action="?/saveEmployment"
      class="space-y-4"
      use:enhance={busy.wrap("save", () => ({ result, update }) => {
        // A failure keeps the wizard where it is and shows the error; success re-mounts it into
        // the receipt (the host keys on it), so nothing here has to move the step.
        started = result.type !== "success";
        void update({ reset: false });
      })}
    >
      <input type="hidden" name="user_id" value={userId} />
      <input type="hidden" name="inherit" value={String(inherit)} />
      <input type="hidden" name="free_time_mode" value={freeTimeMode} />
      {#if mode === "edit" && openContract}
        <input type="hidden" name="contract_id" value={openContract.id} />
      {/if}

      <!-- ─── Step 1: the contract ─────────────────────────────────────────────── -->
      <div class:hidden={step !== 1} class="space-y-4">
        {#if contracts.length > 0}
          <ul class="divide-y divide-border rounded-lg border border-border">
            {#each contracts as contract (contract.id)}
              <li class="flex items-center gap-3 px-3 py-2 text-sm">
                <div class="min-w-0 flex-1">
                  <span class="font-medium text-text">
                    {t("settings.users.contract_hours_value", {
                      hours: fmtHours(contract.contract_hours_per_week),
                    })}
                  </span>
                  <span class="block text-xs text-text-muted">
                    {fmtNumericDate(contract.start_date)} → {contract.end_date
                      ? fmtNumericDate(contract.end_date)
                      : t("settings.users.contract_open")}
                    · {t("settings.users.contract_scheduled", {
                      hours: fmtHours(contract.scheduled_hours_per_week),
                    })}
                    {#if Number(contract.effective_free_time_per_week ?? 0) > 0}
                      · {t("settings.employment.free_time_short", {
                        hours: fmtHours(contract.effective_free_time_per_week ?? 0),
                      })}
                    {/if}
                  </span>
                </div>
                {#if !contract.end_date}
                  <Button
                    variant="secondary"
                    size="xs"
                    type="button"
                    onclick={() => onterminate?.(contract)}
                  >
                    {t("settings.users.contract_terminate")}
                  </Button>
                {/if}
                <button
                  type="button"
                  onclick={() => {
                    deleteId = contract.id;
                    deleteOpen = true;
                  }}
                  class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  title={t("common.delete")}
                  aria-label={t("common.delete")}
                >
                  <Trash2 size={14} />
                </button>
              </li>
            {/each}
          </ul>
        {/if}

        {#if mode === "edit" && openContract}
          <div class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.employment.editing_current")}
            <button
              type="button"
              class="ml-1 font-medium text-brand hover:underline"
              onclick={startNewContract}
            >
              {t("settings.employment.start_new")}
            </button>
          </div>
        {:else}
          <div class="space-y-3 border-t border-border pt-4">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
                {t("settings.employment.new_period")}
              </p>
              {#if openContract}
                <button
                  type="button"
                  class="text-xs font-medium text-brand hover:underline"
                  onclick={useCurrentContract}
                >
                  {t("settings.employment.back_to_current")}
                </button>
              {/if}
            </div>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <label for="e-start" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_start")}</label
                >
                <!-- Deliberately not `required`: the steps are hidden with CSS, not unmounted
                     (the fields have to survive to the save on step 3), and a `required` control
                     inside a `display:none` block blocks the submit with nothing on screen to
                     explain it. `step1Ready` gates Volgende and the API validates again. -->
                <DateInput
                  id="e-start"
                  name="start_date"
                  formId="employment-form"
                  bind:value={startDate}
                />
              </div>
              <div>
                <label for="e-end" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_end")}</label
                >
                <DateInput
                  id="e-end"
                  name="end_date"
                  formId="employment-form"
                  bind:value={endDate}
                />
              </div>
              <div>
                <label for="e-hours" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_hours")}</label
                >
                <input
                  id="e-hours"
                  name="contract_hours_per_week"
                  inputmode="decimal"
                  placeholder="36"
                  bind:value={contractHours}
                  class={inputClass}
                />
              </div>
            </div>
            <p class="text-xs text-text-muted">{t("settings.employment.contract_hint")}</p>
          </div>
        {/if}
      </div>

      <!-- ─── Step 2: the week, and what it earns ──────────────────────────────── -->
      <div class:hidden={step !== 2} class="space-y-4">
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={inherit} class="h-4 w-4 rounded border-border" />
          {t("settings.users.schedule_inherit")}
        </label>
        {#if inherit}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.users.schedule_inherited_hint", { hours: fmtHours(norm) })}
          </p>
        {/if}

        <!-- The one choice the system cannot infer, asked outright. Guessing it from the schedule
             is exactly how a four-day part-timer ended up with a second pot of free days. -->
        <fieldset class="space-y-2 border-t border-border pt-4">
          <legend class="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
            {t("settings.employment.free_time_question")}
          </legend>
          <label class={radioRow}>
            <input
              type="radio"
              class="mt-0.5"
              value="derive"
              bind:group={freeTimeMode}
              name="free_time_mode_choice"
            />
            <span>
              <span class="font-medium text-text">{t("settings.employment.ft_derive")}</span>
              <span class="mt-0.5 block text-xs text-text-muted">
                {t("settings.employment.ft_derive_hint", {
                  norm: fmtHours(norm),
                  contract: fmtHours(enteredHours),
                  hours: fmtHours(Math.max(0, norm - enteredHours)),
                })}
              </span>
            </span>
          </label>
          <label class={radioRow}>
            <input
              type="radio"
              class="mt-0.5"
              value="roster"
              bind:group={freeTimeMode}
              name="free_time_mode_choice"
            />
            <span>
              <span class="font-medium text-text">{t("settings.employment.ft_roster")}</span>
              <span class="mt-0.5 block text-xs text-text-muted"
                >{t("settings.employment.ft_roster_hint")}</span
              >
            </span>
          </label>
          <label class={radioRow}>
            <input
              type="radio"
              class="mt-0.5"
              value="custom"
              bind:group={freeTimeMode}
              name="free_time_mode_choice"
            />
            <span class="min-w-0 flex-1">
              <span class="font-medium text-text">{t("settings.employment.ft_custom")}</span>
              <span class="mt-0.5 block text-xs text-text-muted"
                >{t("settings.employment.ft_custom_hint")}</span
              >
              {#if freeTimeMode === "custom"}
                <input
                  name="free_time_hours"
                  inputmode="decimal"
                  placeholder="4"
                  bind:value={freeTimeCustom}
                  class="{inputClass} mt-2 max-w-[8rem]"
                />
              {/if}
            </span>
          </label>
        </fieldset>

        <!-- Three numbers, one line: the relationship that was invisible before. -->
        <dl class="grid grid-cols-3 gap-3 rounded-lg bg-surface p-3 text-sm">
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.sum_contract")}</dt>
            <dd class="font-medium text-text">{fmtHours(enteredHours)}</dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.sum_roster")}</dt>
            <dd class="font-medium text-text">{fmtHours(rosterHours)}</dd>
          </div>
          <div>
            <dt class="text-xs text-text-muted">{t("settings.employment.sum_free_time")}</dt>
            <dd class="font-medium {freeTimePerWeek > 0 ? 'text-brand' : 'text-text'}">
              {fmtHours(freeTimePerWeek)}
            </dd>
          </div>
        </dl>
        {#if freeTimePerWeek > 0}
          <p class="text-xs text-text-muted">
            {t("settings.employment.free_days_estimate", {
              hours: fmtHours(freeTimePerWeek),
              days: fmtHours(freeDaysPerYear),
            })}
          </p>
        {/if}
      </div>

      <!-- ─── Step 3: placing the free days ───────────────────────────────────── -->
      <!-- The existing patterns and their actions live *outside* this form (just above it) —
           an activate/deactivate submit cannot be a `<form>` nested inside another one. -->
      <div class:hidden={step !== 3} class="space-y-4">
        {#if !canPlan}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {freeTimeType === null
              ? t("settings.employment.no_free_time_type")
              : t("settings.employment.no_free_time")}
          </p>
        {:else}
          <input type="hidden" name="leave_type_id" value={freeTimeType?.id ?? ""} />
          <input type="hidden" name="pattern_mode" value={patternMode} />
          <p class="text-sm text-text-muted">
            {t("settings.employment.plan_intro", {
              days: fmtHours(freeDaysPerYear),
              type: typeLabel(freeTimeType ?? undefined, locale),
            })}
          </p>

          <div class="space-y-2">
            <label class={radioRow}>
              <input type="radio" class="mt-0.5" value="spread" bind:group={patternMode} />
              <span>
                <span class="font-medium text-text">{t("settings.employment.plan_spread")}</span>
                <span class="mt-0.5 block text-xs text-text-muted"
                  >{t("settings.employment.plan_spread_hint")}</span
                >
              </span>
            </label>
            <label class={radioRow}>
              <input type="radio" class="mt-0.5" value="interval" bind:group={patternMode} />
              <span>
                <span class="font-medium text-text">{t("settings.employment.plan_interval")}</span>
                <span class="mt-0.5 block text-xs text-text-muted"
                  >{t("settings.employment.plan_interval_hint")}</span
                >
              </span>
            </label>
            <label class={radioRow}>
              <input type="radio" class="mt-0.5" value="none" bind:group={patternMode} />
              <span>
                <span class="font-medium text-text">{t("settings.employment.plan_none")}</span>
                <span class="mt-0.5 block text-xs text-text-muted"
                  >{t("settings.employment.plan_none_hint")}</span
                >
              </span>
            </label>
          </div>

          {#if patternMode !== "none"}
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label for="e-anchor" class="mb-1 block text-xs text-text-muted"
                  >{t("leave.recurring.first_day")}</label
                >
                <DateInput
                  id="e-anchor"
                  name="anchor_date"
                  formId="employment-form"
                  bind:value={anchorDate}
                />
                <p class="mt-1 text-xs text-text-muted">
                  {t("settings.employment.anchor_hint")}
                </p>
              </div>
              {#if patternMode === "spread"}
                <div>
                  <label for="e-days" class="mb-1 block text-xs text-text-muted"
                    >{t("settings.employment.days_per_year")}</label
                  >
                  <input
                    id="e-days"
                    name="days_per_year"
                    inputmode="numeric"
                    bind:value={daysPerYear}
                    class={inputClass}
                  />
                </div>
              {:else}
                <div>
                  <label for="e-interval" class="mb-1 block text-xs text-text-muted"
                    >{t("leave.recurring.interval")}</label
                  >
                  <select
                    id="e-interval"
                    name="interval_weeks"
                    bind:value={intervalWeeks}
                    class={inputClass}
                  >
                    {#each [1, 2, 3, 4, 6, 8] as weeks (weeks)}
                      <option value={weeks}>
                        {weeks === 1
                          ? t("leave.recurring.every_week")
                          : t("leave.recurring.every_n", { n: weeks })}
                      </option>
                    {/each}
                  </select>
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
                    <label for="e-start-time" class="mb-1 block text-xs text-text-muted"
                      >{t("leave.form.start_time")}</label
                    >
                    <TimeInput
                      id="e-start-time"
                      name="start_time"
                      formId="employment-form"
                      bind:value={startTime}
                    />
                  </div>
                  <div>
                    <label for="e-end-time" class="mb-1 block text-xs text-text-muted"
                      >{t("leave.form.end_time")}</label
                    >
                    <TimeInput
                      id="e-end-time"
                      name="end_time"
                      formId="employment-form"
                      bind:value={endTime}
                    />
                  </div>
                </div>
                <p class="mt-1 text-xs text-text-muted">{t("leave.form.times_hint")}</p>
              {/if}
            </div>
          {/if}
        {/if}
      </div>

      {#if form?.error && (started || !form?.employmentSaved)}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}

      <div class="flex items-center justify-between border-t border-border pt-4">
        <button
          type="button"
          class="flex items-center gap-1.5 text-sm text-text-muted hover:text-text disabled:invisible"
          disabled={step === 1}
          onclick={() => (step -= 1)}
        >
          <ArrowLeft size={14} />
          {t("common.back")}
        </button>
        {#if step < 3}
          <Button
            type="button"
            disabled={!step1Ready}
            onclick={() => (step === 1 ? (step = 2) : goPattern())}
          >
            <span class="flex items-center gap-1.5">
              {t("common.next")}
              <ArrowRight size={14} />
            </span>
          </Button>
        {:else}
          <Button loading={busy.is("save")}>{t("common.save")}</Button>
        {/if}
      </div>
    </form>
  {/if}
</div>

<ConfirmDialog
  bind:open={deleteOpen}
  title={t("common.delete")}
  message={t("settings.employment.contract_delete_confirm")}
  action="?/deleteContract"
  fields={{ contract_id: deleteId }}
  confirmLabel={t("common.delete")}
/>

<RecurringDeleteDialog
  bind:open={deletePatternOpen}
  patternId={deletePattern?.id ?? ""}
  upcomingDays={deletePattern?.upcoming_days ?? 0}
/>
