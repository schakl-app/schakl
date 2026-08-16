<script lang="ts">
  /**
   * The repeat rule, composed as a sentence (#335 phase 2 + 5).
   *
   * What it replaces: a frequency select, an **unlabelled number box** whose "Elke" was aria-only,
   * a Modus select, and one dense paragraph explaining both modes at once. Read in order that was
   * "Maandelijks · 1 · Na afronden"; the thought behind it is "elke maand, op dag 1, na afronden".
   * So every part is labelled, the interval and its unit are one phrase, the two modes are radios
   * that each carry their own line of explanation, and the whole thing ends in a **real date** the
   * API resolved — not a rule the user has to simulate in their head.
   *
   * Every field joins the page's one `task-edit` form (`form={formId}`), so the rule is saved by
   * the same Opslaan as the title: one save per editing surface (docs/UX.md §3).
   *
   * The preview is fetched, never derived. Clamping (31 → 28/29/30), leap years, "not in the
   * past" and the org's own today all live in `app/modules/tasks/recurrence.py`; re-implementing
   * them here would be a second opinion about a question the API already answers (#312).
   */
  import { untrack } from "svelte";

  import { fmtDayMonthYear, monthNames, weekdayNames } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import { memberArchivedLabel, splitMemberOptions } from "$lib/core/members";

  import { anchorKind, clockOf, FREQS, type Recurrence, type RecurrenceFreq } from "./recurrence";

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
    is_active?: boolean;
  }

  let {
    formId,
    previewUrl,
    recurrence = null,
    dueDate = null,
    allocatedMinutes = null,
    assigneeUserId = null,
    lastBlockStart = null,
    members = [],
    currentUserId,
    canSchedule = false,
    canScheduleAny = false,
  }: {
    formId: string;
    /**
     * Where the preview is fetched from — a `+server.ts` beside the task page, never
     * `/api/v1/...` straight from the browser: only traefik routes that prefix, so the same call
     * 404s on every dev server and the preview would silently never appear.
     */
    previewUrl: string;
    recurrence?: Recurrence | null;
    dueDate?: string | null;
    /** Prefills the auto-plan length — the budget is what the task already says it takes. */
    allocatedMinutes?: number | null;
    assigneeUserId?: string | null;
    /** The carrier's last hand-planned block's start ("09:00"), the best guess at the hour. */
    lastBlockStart?: string | null;
    members?: Member[];
    currentUserId: string;
    canSchedule?: boolean;
    canScheduleAny?: boolean;
  } = $props();

  // svelte-ignore state_referenced_locally
  let freq = $state<RecurrenceFreq | "">(recurrence?.freq ?? "");
  // svelte-ignore state_referenced_locally
  let interval = $state(String(recurrence?.interval ?? 1));
  // svelte-ignore state_referenced_locally
  let mode = $state(recurrence?.mode ?? "after_completion");
  // "" is the deliberate "volg de vervaldatum" option, not an unfilled field — which is why the
  // hint below says in words what the absent anchor means (the current invisible truth, #335 F2).
  // svelte-ignore state_referenced_locally
  let onWeekday = $state(recurrence?.on_weekday != null ? String(recurrence.on_weekday) : "");
  // svelte-ignore state_referenced_locally
  let onDay = $state(recurrence?.on_day != null ? String(recurrence.on_day) : "");
  // svelte-ignore state_referenced_locally
  let onMonth = $state(recurrence?.on_month != null ? String(recurrence.on_month) : "");
  // svelte-ignore state_referenced_locally
  let planOn = $state(!!recurrence?.plan);
  // svelte-ignore state_referenced_locally
  let planUser = $state(recurrence?.plan?.user_id ?? "");
  // svelte-ignore state_referenced_locally
  let planStart = $state(clockOf(recurrence?.plan?.start_time) || lastBlockStart || "09:00");
  // svelte-ignore state_referenced_locally
  let planMinutes = $state<number | null>(
    recurrence?.plan?.duration_minutes ?? (allocatedMinutes || 60),
  );

  const kind = $derived(freq ? anchorKind(freq as RecurrenceFreq) : "none");
  const weekdays = $derived(weekdayNames());
  const months = $derived(monthNames());
  // A rule saved today books occurrences for months, so a deactivated account is the worst
  // possible answer here: nobody is ever reminded. Behind the search, wearing its state — and
  // still offered outright while the rule already names them, or editing an inherited rule
  // would silently reassign it.
  const personPicker = $derived(splitMemberOptions(members, { selectedId: planUser }));
  const personOptions = $derived(personPicker.live);

  /**
   * Ticking the box prefills from what the screen already knows (#335): the assignee, the time
   * budget, and the hour of the last block someone planned by hand. Planning the first occurrence
   * yourself and then ticking this is the expected path, so the box should already say what you
   * just did rather than making you say it twice.
   */
  function onPlanToggled(next: boolean) {
    planOn = next;
    if (!next) return;
    if (!planUser && canScheduleAny) planUser = assigneeUserId ?? "";
    // The budget wins when the task has one — that *is* what the task says it takes, and the
    // 60-minute default is only there for a task that says nothing.
    planMinutes = allocatedMinutes || planMinutes || 60;
    if (lastBlockStart) planStart = lastBlockStart;
  }

  // Switching frequency drops an anchor the new one cannot honour — the API refuses the pair
  // (422), so leaving a stale `on_day` on a weekly rule would make Opslaan fail on a field the
  // form is no longer even showing.
  // svelte-ignore state_referenced_locally
  let lastKind = $state(kind);
  $effect(() => {
    const current = kind;
    untrack(() => {
      if (current === lastKind) return;
      lastKind = current;
      if (current !== "weekday") onWeekday = "";
      if (current === "none" || current === "weekday") onDay = "";
      if (current !== "date") onMonth = "";
    });
  });

  // --- the preview line ------------------------------------------------------------------ //
  interface Preview {
    next_date: string;
    following: string[];
    on_completion: boolean;
    planned_start: string | null;
    planned_end: string | null;
  }
  let preview = $state<Preview | null>(null);
  let previewFailed = $state(false);

  /** The body the API would be saved, built from the live controls — one shape, both uses. */
  const rulePayload = $derived.by(() => {
    if (!freq) return null;
    const rule: Record<string, unknown> = {
      freq,
      interval: Math.max(1, Number(interval) || 1),
      mode,
    };
    if (kind === "weekday" && onWeekday !== "") rule.on_weekday = Number(onWeekday);
    if ((kind === "day" || kind === "date") && onDay !== "") rule.on_day = Number(onDay);
    if (kind === "date" && onMonth !== "") rule.on_month = Number(onMonth);
    // A yearly anchor is a whole date or nothing: half of one is not a day the API can resolve.
    if (kind === "date" && (rule.on_day == null || rule.on_month == null)) {
      delete rule.on_day;
      delete rule.on_month;
    }
    if (planOn && planStart && planMinutes) {
      rule.plan = {
        user_id: planUser || null,
        start_time: `${planStart}:00`,
        duration_minutes: planMinutes,
      };
    }
    return rule;
  });

  $effect(() => {
    const rule = rulePayload;
    const due = dueDate;
    if (!rule) {
      preview = null;
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(previewUrl, {
          method: "POST",
          headers: { "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify({ recurrence: rule, due_date: due || null }),
        });
        if (cancelled) return;
        // A refusal here is a rule the API would refuse to store — say so rather than leaving
        // the last good date on screen, which would read as approval of what is now typed.
        previewFailed = !res.ok;
        preview = res.ok ? await res.json() : null;
      } catch {
        if (!cancelled) {
          previewFailed = true;
          preview = null;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  });

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const numberClass =
    "w-20 rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-3">
  <div>
    <label for="rec-freq" class="mb-1 block text-xs font-medium text-text-muted">
      {t("tasks.recurrence.title")}
    </label>
    <select id="rec-freq" name="freq" form={formId} bind:value={freq} class={inputClass}>
      <option value="">{t("tasks.recurrence.none")}</option>
      {#each FREQS as f (f)}
        <option value={f}>{t(`tasks.recurrence.freq.${f}`)}</option>
      {/each}
    </select>
  </div>

  {#if freq}
    <!-- "Elke [1] maand" — the number and its unit read as one phrase, which is what the bare
         box could never do. The unit word follows the interval so the plural agrees with it. -->
    <div class="flex flex-wrap items-end gap-2">
      <div>
        <label for="rec-interval" class="mb-1 block text-xs font-medium text-text-muted">
          {t("tasks.recurrence.interval")}
        </label>
        <input
          id="rec-interval"
          name="interval"
          type="number"
          min="1"
          max="365"
          form={formId}
          bind:value={interval}
          class={numberClass}
        />
      </div>
      <span class="pb-2 text-sm text-text">
        {Number(interval) === 1
          ? t(`tasks.recurrence.plain.${freq}_one`)
          : t(`tasks.recurrence.plain.${freq}_other`)}
      </span>
    </div>

    {#if kind === "weekday"}
      <div>
        <label for="rec-weekday" class="mb-1 block text-xs font-medium text-text-muted">
          {t("tasks.recurrence.anchor.weekday")}
        </label>
        <select
          id="rec-weekday"
          name="on_weekday"
          form={formId}
          bind:value={onWeekday}
          class={inputClass}
        >
          <option value="">{t("tasks.recurrence.anchor.follow_due")}</option>
          {#each weekdays as name, index (name)}
            <option value={String(index)}>{name}</option>
          {/each}
        </select>
      </div>
    {:else if kind === "day"}
      <div>
        <label for="rec-day" class="mb-1 block text-xs font-medium text-text-muted">
          {t("tasks.recurrence.anchor.day")}
        </label>
        <input
          id="rec-day"
          name="on_day"
          type="number"
          min="1"
          max="31"
          placeholder={t("tasks.recurrence.anchor.follow_due")}
          form={formId}
          bind:value={onDay}
          class={numberClass}
        />
        <p class="mt-1 text-[11px] leading-snug text-text-muted">
          {t("tasks.recurrence.anchor.day_hint")}
        </p>
      </div>
    {:else if kind === "date"}
      <div class="flex flex-wrap items-end gap-2">
        <div>
          <label for="rec-day" class="mb-1 block text-xs font-medium text-text-muted">
            {t("tasks.recurrence.anchor.day")}
          </label>
          <input
            id="rec-day"
            name="on_day"
            type="number"
            min="1"
            max="31"
            form={formId}
            bind:value={onDay}
            class={numberClass}
          />
        </div>
        <div class="min-w-[8rem] flex-1">
          <label for="rec-month" class="mb-1 block text-xs font-medium text-text-muted">
            {t("tasks.recurrence.anchor.month")}
          </label>
          <select
            id="rec-month"
            name="on_month"
            form={formId}
            bind:value={onMonth}
            class={inputClass}
          >
            <option value="">{t("tasks.recurrence.anchor.follow_due")}</option>
            {#each months as name, index (name)}
              <option value={String(index + 1)}>{name}</option>
            {/each}
          </select>
        </div>
      </div>
    {/if}

    <!-- What the *absent* anchor means, said out loud. It was the rule all along — the cadence
         hangs off the due date — and nothing on any screen admitted it (#335 F2). -->
    {#if kind !== "none" && !(kind === "weekday" ? onWeekday : onDay)}
      <p class="text-[11px] leading-snug text-text-muted">
        {dueDate
          ? t("tasks.recurrence.anchor.follow_due_hint", { date: fmtDayMonthYear(dueDate) })
          : t("tasks.recurrence.anchor.follow_due_hint_none")}
      </p>
    {/if}

    <!-- Two radios with a line each, instead of one paragraph explaining both at once. -->
    <fieldset class="space-y-2">
      <legend class="mb-1 text-xs font-medium text-text-muted">
        {t("tasks.recurrence.mode")}
      </legend>
      {#each ["after_completion", "schedule"] as m (m)}
        <label class="flex items-start gap-2 text-sm text-text">
          <input
            type="radio"
            name="mode"
            value={m}
            form={formId}
            checked={mode === m}
            onchange={() => (mode = m as "after_completion" | "schedule")}
            class="mt-0.5 shrink-0"
          />
          <span>
            <span class="font-medium">{t(`tasks.recurrence.mode.${m}`)}</span>
            <span class="mt-0.5 block text-[11px] leading-snug text-text-muted">
              {t(`tasks.recurrence.mode.${m}_hint`)}
            </span>
          </span>
        </label>
      {/each}
    </fieldset>

    <!-- Herhaal ook de planning. Gated on the keys Inplannen itself declares, not on the task
         write this form rides in on: putting a block on a colleague's calendar is a different
         capability, and the generator will later execute this decision as the system. -->
    {#if canSchedule}
      <div class="rounded-lg border border-border bg-surface p-3">
        <label class="flex items-start gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="plan_enabled"
            value="true"
            form={formId}
            checked={planOn}
            onchange={(e) => onPlanToggled(e.currentTarget.checked)}
            class="mt-0.5 shrink-0"
          />
          <span>
            <span class="font-medium">{t("tasks.recurrence.plan.enable")}</span>
            <span class="mt-0.5 block text-[11px] leading-snug text-text-muted">
              {t("tasks.recurrence.plan.enable_hint")}
            </span>
          </span>
        </label>
        {#if planOn}
          <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <span class="mb-1 block text-xs font-medium text-text-muted">
                {t("tasks.recurrence.plan.person")}
              </span>
              {#if canScheduleAny}
                <Combobox
                  name="plan_user_id"
                  {formId}
                  bind:value={planUser}
                  items={personOptions}
                  placeholder={t("tasks.recurrence.plan.person_assignee")}
                  archived={personPicker.retired}
                  archivedLabel={memberArchivedLabel()}
                />
              {:else}
                <!-- `:own` plans only yourself, so the field states that rather than offering a
                     picker whose every other option the API would refuse. -->
                <input type="hidden" name="plan_user_id" value={currentUserId} form={formId} />
                <p class="rounded-lg border border-border px-3 py-2 text-sm text-text-muted">
                  {t("tasks.schedule.you")}
                </p>
              {/if}
            </div>
            <div>
              <label for="rec-plan-start" class="mb-1 block text-xs font-medium text-text-muted">
                {t("tasks.recurrence.plan.start")}
              </label>
              <TimeInput id="rec-plan-start" name="plan_start" {formId} bind:value={planStart} />
            </div>
            <div>
              <label for="rec-plan-duration" class="mb-1 block text-xs font-medium text-text-muted">
                {t("tasks.recurrence.plan.duration")}
              </label>
              <DurationInput
                id="rec-plan-duration"
                name="plan_duration"
                {formId}
                bind:minutes={planMinutes}
              />
            </div>
          </div>
          <p class="mt-2 text-[11px] leading-snug text-text-muted">
            {t("tasks.recurrence.plan.day_hint")}
          </p>
        {/if}
      </div>
    {/if}

    <!-- The sentence ends in a date the API resolved, while it is being composed. -->
    <p class="rounded-lg bg-surface px-3 py-2 text-sm">
      {#if previewFailed}
        <span class="text-amber-600 dark:text-amber-400">
          {t("tasks.recurrence.preview_invalid")}
        </span>
      {:else if preview}
        <span class="font-medium text-text">{t("tasks.recurrence.next")}:</span>
        <span class="text-text">
          {fmtDayMonthYear(preview.next_date)}{preview.planned_start
            ? `, ${preview.planned_start.slice(0, 5)}-${(preview.planned_end ?? "").slice(0, 5)}`
            : ""}
        </span>
        {#if preview.on_completion}
          <span class="mt-0.5 block text-[11px] leading-snug text-text-muted">
            {t("tasks.recurrence.next_on_completion")}
          </span>
        {:else if preview.following.length > 0}
          <span class="mt-0.5 block text-[11px] leading-snug text-text-muted">
            {t("tasks.recurrence.then", {
              dates: preview.following.map((d) => fmtDayMonthYear(d)).join(" · "),
            })}
          </span>
        {/if}
      {:else}
        <span class="text-text-muted">—</span>
      {/if}
    </p>

    <!-- What travels and what does not, stated where the rule is written rather than discovered
         a month later when the briefing link is missing from the new occurrence. -->
    <details class="text-[11px] leading-snug text-text-muted">
      <summary class="cursor-pointer font-medium">{t("tasks.recurrence.carries.title")}</summary>
      <p class="mt-1">{t("tasks.recurrence.carries")}</p>
      <p class="mt-1">{t("tasks.recurrence.carries_not")}</p>
    </details>
  {/if}
</div>
