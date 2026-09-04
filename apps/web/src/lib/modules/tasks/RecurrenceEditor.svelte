<script lang="ts">
  /**
   * The repeat rule, composed as a sentence (#335 phase 2 + 5), and the blocks each occurrence
   * books — several, each on a day stated *relative to the occurrence* (docs/UX.md).
   *
   * What it replaces: a frequency select, an **unlabelled number box** whose "Elke" was aria-only,
   * a Modus select, and one dense paragraph explaining both modes at once. Read in order that was
   * "Maandelijks · 1 · Na afronden"; the thought behind it is "elke maand, op dag 1, na afronden".
   * So every part is labelled, the interval and its unit are one phrase, the two modes are radios
   * that each carry their own line of explanation, and the cadence controls have a **real date**
   * the API resolved beside them — not a rule the user has to simulate in their head. Beside,
   * not at the foot: the date is the cadence's answer, and it used to sit below the mode and
   * every planned block, a screen away from the controls that change it.
   *
   * The plan was one clock on the deadline. A recurring job is rarely one sitting: the newsletter
   * is drafted on the Tuesday before, reviewed on the Thursday, sent on the first — so the plan is
   * a list of blocks now, each placed ("2 dagen ervoor", "de tweede dinsdag van de maand"), each
   * with its own people, and the preview resolves every one of them to a date for the next
   * occurrence. The list travels as one JSON field (`plan_blocks`): a list of placed blocks is
   * not a shape a flat form can post as fields, and this editor is the one place it is composed.
   *
   * Every field joins the page's one `task-edit` form (`form={formId}`), so the rule is saved by
   * the same Opslaan as the title: one save per editing surface (docs/UX.md §3).
   *
   * The preview is fetched, never derived. Clamping (31 → 28/29/30), leap years, "not in the
   * past" and the org's own today all live in `app/modules/tasks/recurrence.py`; re-implementing
   * them here would be a second opinion about a question the API already answers (#312).
   */
  import { Plus, X } from "@lucide/svelte";
  import { untrack } from "svelte";

  import {
    capitalizeFirst,
    fmtDayMonthYear,
    fmtWeekdayShort,
    monthNames,
    RANGE_DASH,
    weekdayNames,
  } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import MembersPicker from "$lib/core/ui/MembersPicker.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import {
    anchorKind,
    clockOf,
    FREQS,
    planBlocks,
    WEEKS,
    weekLabel,
    type PlanBlock,
    type Recurrence,
    type RecurrenceFreq,
  } from "./recurrence";

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
  let onWeek = $state(recurrence?.on_week != null ? String(recurrence.on_week) : "");
  // How a monthly-style rule is pinned: a day number, an n-th weekday, or the due date. One
  // control rather than two boxes that could both be filled: the API refuses the pair.
  // svelte-ignore state_referenced_locally
  let anchorMode = $state<"follow" | "day" | "nth">(
    recurrence?.on_week != null ? "nth" : recurrence?.on_day != null ? "day" : "follow",
  );

  // --- the plan: placed blocks ------------------------------------------------------------- //
  type Placement = "due" | "before" | "after" | "weekday" | "nth" | "day";
  interface Row {
    key: number;
    placement: Placement;
    days: string;
    weekday: string;
    week: string;
    day: string;
    userIds: string[];
    start: string;
    minutes: number | null;
    note: string;
  }
  let nextKey = 1;
  function rowFrom(block: PlanBlock): Row {
    const placement: Placement =
      block.on === "offset"
        ? (block.days ?? 0) < 0
          ? "before"
          : "after"
        : block.on === "weekday"
          ? block.week != null
            ? "nth"
            : "weekday"
          : block.on;
    return {
      key: nextKey++,
      placement,
      days: String(Math.abs(block.days ?? 1) || 1),
      weekday: String(block.weekday ?? 1),
      week: String(block.week ?? 1),
      day: String(block.day ?? 1),
      userIds: block.user_ids ?? [],
      start: clockOf(block.start_time) || "09:00",
      minutes: block.duration_minutes,
      note: block.note ?? "",
    };
  }
  /** A fresh block, prefilled from what the screen already knows (#335): the assignee, the time
   *  budget, the hour of the last hand-planned block. */
  function freshRow(): Row {
    return {
      key: nextKey++,
      placement: "due",
      days: "1",
      weekday: "1",
      week: "1",
      day: "1",
      userIds: [],
      start: lastBlockStart || "09:00",
      minutes: allocatedMinutes || 60,
      note: "",
    };
  }
  // svelte-ignore state_referenced_locally
  let rows = $state<Row[]>(planBlocks(recurrence).map(rowFrom));
  // svelte-ignore state_referenced_locally
  let planOn = $state(rows.length > 0);

  function onPlanToggled(next: boolean) {
    planOn = next;
    if (next && rows.length === 0) rows = [freshRow()];
  }
  function addRow() {
    const last = rows[rows.length - 1];
    const row = freshRow();
    if (last) {
      // The second block is usually "the same, but earlier": inherit the people and length.
      row.userIds = [...last.userIds];
      row.minutes = last.minutes;
      row.placement = "before";
    }
    rows = [...rows, row];
  }
  function removeRow(key: number) {
    rows = rows.filter((row) => row.key !== key);
    if (rows.length === 0) planOn = false;
  }

  /** A row as the block the API stores — `null` while the row is not yet a whole block. */
  function blockOf(row: Row): PlanBlock | null {
    if (!row.start || !row.minutes) return null;
    const block: PlanBlock = {
      on: "due",
      start_time: `${row.start}:00`,
      duration_minutes: row.minutes,
      note: row.note.trim() || null,
      // A `:own` holder plans one calendar: their own — never the roster, which may name
      // colleagues the API would refuse them.
      user_ids: canScheduleAny ? (row.userIds.length ? row.userIds : null) : [currentUserId],
    };
    const days = Math.max(1, Number(row.days) || 1);
    switch (row.placement) {
      case "before":
        return { ...block, on: "offset", days: -days };
      case "after":
        return { ...block, on: "offset", days };
      case "weekday":
        return { ...block, on: "weekday", weekday: Number(row.weekday) };
      case "nth":
        return { ...block, on: "weekday", weekday: Number(row.weekday), week: Number(row.week) };
      case "day":
        return { ...block, on: "day", day: Math.min(31, Math.max(1, Number(row.day) || 1)) };
      default:
        return block;
    }
  }
  const blocks = $derived(
    planOn ? rows.map(blockOf).filter((block): block is PlanBlock => block !== null) : [],
  );
  const blocksJson = $derived(JSON.stringify(blocks));

  const kind = $derived(freq ? anchorKind(freq as RecurrenceFreq) : "none");
  const weekdays = $derived(weekdayNames());
  const months = $derived(monthNames());
  const memberName = $derived(new Map(members.map((m) => [m.user_id, memberLabel(m)])));
  // Placements a weekly rule can use: "the n-th weekday of the month" and "day N of the month"
  // say nothing about a week, so they are not offered for one.
  const placements = $derived<Placement[]>(
    kind === "weekday" || kind === "none"
      ? ["due", "before", "after", "weekday"]
      : ["due", "before", "after", "weekday", "nth", "day"],
  );

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
      if (current === "none" || current === "weekday") {
        onDay = "";
        onWeek = "";
        anchorMode = "follow";
      }
      if (current !== "date") onMonth = "";
      for (const row of rows) {
        if ((row.placement === "nth" || row.placement === "day") && current === "weekday") {
          row.placement = "due";
        }
      }
    });
  });

  // --- the preview line ------------------------------------------------------------------ //
  interface PreviewBlock {
    on: string;
    day: string;
    start_time: string;
    end_time: string;
    user_ids: string[] | null;
    in_past: boolean;
  }
  interface Preview {
    next_date: string;
    following: string[];
    on_completion: boolean;
    blocks: PreviewBlock[];
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
    if ((kind === "day" || kind === "date") && anchorMode === "day" && onDay !== "") {
      rule.on_day = Number(onDay);
    }
    if (
      (kind === "day" || kind === "date") &&
      anchorMode === "nth" &&
      onWeek !== "" &&
      onWeekday !== ""
    ) {
      rule.on_weekday = Number(onWeekday);
      rule.on_week = Number(onWeek);
    }
    if (kind === "date" && onMonth !== "" && (rule.on_day != null || rule.on_week != null)) {
      rule.on_month = Number(onMonth);
    }
    // A yearly anchor is a whole date or nothing: half of one is not a day the API can resolve.
    if (kind === "date" && rule.on_month == null) {
      delete rule.on_day;
      delete rule.on_weekday;
      delete rule.on_week;
    }
    if (blocks.length) rule.plan = { blocks };
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

  function previewPeople(block: PreviewBlock): string {
    if (!block.user_ids?.length) return t("tasks.recurrence.plan.roster");
    return block.user_ids
      .map((id) => (id === currentUserId ? t("tasks.schedule.you") : (memberName.get(id) ?? "—")))
      .join(", ");
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const numberClass =
    "w-20 rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const smallClass =
    "rounded-lg border border-border px-2 py-1.5 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="@container space-y-3">
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
    <!-- The cadence and its answer, side by side: the controls that decide *when* on the left,
         the date they resolve to on the right — under them where the card is too narrow, and the
         split follows the *card* (`@container`), not the viewport: the same editor sits in a
         half-width card on one screen and a full-width form on another. It used to sit at
         the foot of the editor, below the mode and every planned block — so on a rule with three
         blocks the one number the cadence controls exist to produce was a screen away from them,
         and changing "op dag 1" to "op dag 15" showed nothing where the eye was. -->
    <div
      class="grid grid-cols-1 gap-3 @md:grid-cols-[minmax(0,1fr)_minmax(0,15rem)] @md:items-start"
    >
      <div class="space-y-3">
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
        {:else if kind === "day" || kind === "date"}
          <!-- One control for *how* the month is pinned, then only the boxes that mode needs. The
           posted anchor fields are rendered per mode, so a stale pair can never reach the API. -->
          <div class="flex flex-wrap items-end gap-2">
            <div class="min-w-[11rem] flex-1">
              <label for="rec-anchor-mode" class="mb-1 block text-xs font-medium text-text-muted">
                {t("tasks.recurrence.anchor.weekday")}
              </label>
              <select id="rec-anchor-mode" bind:value={anchorMode} class={inputClass}>
                <option value="follow">{t("tasks.recurrence.anchor.follow_due")}</option>
                <option value="day">
                  {t(
                    kind === "date"
                      ? "tasks.recurrence.anchor.mode_date"
                      : "tasks.recurrence.anchor.mode_day",
                  )}
                </option>
                <option value="nth">{t("tasks.recurrence.anchor.mode_nth")}</option>
              </select>
            </div>
            {#if anchorMode === "day"}
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
            {:else if anchorMode === "nth"}
              <div>
                <label for="rec-week" class="mb-1 block text-xs font-medium text-text-muted">
                  {t("tasks.recurrence.anchor.week")}
                </label>
                <select
                  id="rec-week"
                  name="on_week"
                  form={formId}
                  bind:value={onWeek}
                  class={smallClass}
                >
                  {#if onWeek === ""}
                    <option value="">—</option>
                  {/if}
                  {#each WEEKS as week (week)}
                    <option value={String(week)}>{weekLabel(week)}</option>
                  {/each}
                </select>
              </div>
              <div>
                <label for="rec-nth-weekday" class="mb-1 block text-xs font-medium text-text-muted">
                  {t("tasks.recurrence.anchor.weekday")}
                </label>
                <select
                  id="rec-nth-weekday"
                  name="on_weekday"
                  form={formId}
                  bind:value={onWeekday}
                  class={smallClass}
                >
                  {#if onWeekday === ""}
                    <option value="">—</option>
                  {/if}
                  {#each weekdays as name, index (name)}
                    <option value={String(index)}>{name}</option>
                  {/each}
                </select>
              </div>
            {/if}
            {#if kind === "date" && anchorMode !== "follow"}
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
                  <option value="">—</option>
                  {#each months as name, index (name)}
                    <option value={String(index + 1)}>{name}</option>
                  {/each}
                </select>
              </div>
            {/if}
          </div>
          {#if anchorMode === "day"}
            <p class="text-[11px] leading-snug text-text-muted">
              {t("tasks.recurrence.anchor.day_hint")}
            </p>
          {/if}
        {/if}

        <!-- What the *absent* anchor means, said out loud. It was the rule all along — the cadence
         hangs off the due date — and nothing on any screen admitted it (#335 F2). -->
        {#if kind !== "none" && (kind === "weekday" ? !onWeekday : anchorMode === "follow")}
          <p class="text-[11px] leading-snug text-text-muted">
            {dueDate
              ? t("tasks.recurrence.anchor.follow_due_hint", { date: fmtDayMonthYear(dueDate) })
              : t("tasks.recurrence.anchor.follow_due_hint_none")}
          </p>
        {/if}
      </div>

      <!-- The sentence ends in a date the API resolved, while it is being composed. -->
      <div class="rounded-lg bg-surface px-3 py-2 text-sm" data-testid="recurrence-preview">
        {#if previewFailed}
          <span class="text-amber-600 dark:text-amber-400">
            {t("tasks.recurrence.preview_invalid")}
          </span>
        {:else if preview}
          <span class="font-medium text-text">{t("tasks.recurrence.next")}:</span>
          <span class="text-text">{fmtDayMonthYear(preview.next_date)}</span>
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
          <span class="font-medium text-text">{t("tasks.recurrence.next")}:</span>
          <span class="text-text-muted">—</span>
        {/if}
      </div>
    </div>

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
        <input type="hidden" name="plan_blocks" value={blocksJson} form={formId} />
        {#if planOn}
          <ul class="mt-3 space-y-2" data-testid="plan-blocks">
            {#each rows as row, i (row.key)}
              <li class="rounded-lg border border-border bg-surface-raised p-2.5">
                <div class="flex flex-wrap items-end gap-2">
                  <div class="min-w-[12rem] flex-1">
                    <label
                      for="rec-plan-{i}-placement"
                      class="mb-1 block text-xs font-medium text-text-muted"
                    >
                      {t("tasks.recurrence.plan.placement")}
                    </label>
                    <select
                      id="rec-plan-{i}-placement"
                      bind:value={row.placement}
                      class="{smallClass} w-full"
                    >
                      {#each placements as option (option)}
                        <option value={option}>
                          {t(`tasks.recurrence.placement.opt.${option}`)}
                        </option>
                      {/each}
                    </select>
                  </div>
                  {#if row.placement === "before" || row.placement === "after"}
                    <div>
                      <label
                        for="rec-plan-{i}-days"
                        class="mb-1 block text-xs font-medium text-text-muted"
                      >
                        {t("tasks.recurrence.plan.days")}
                      </label>
                      <input
                        id="rec-plan-{i}-days"
                        type="number"
                        min="1"
                        max="60"
                        bind:value={row.days}
                        class="{smallClass} w-16"
                      />
                    </div>
                  {:else if row.placement === "weekday" || row.placement === "nth"}
                    {#if row.placement === "nth"}
                      <div>
                        <label
                          for="rec-plan-{i}-week"
                          class="mb-1 block text-xs font-medium text-text-muted"
                        >
                          {t("tasks.recurrence.anchor.week")}
                        </label>
                        <select id="rec-plan-{i}-week" bind:value={row.week} class={smallClass}>
                          {#each WEEKS as week (week)}
                            <option value={String(week)}>{weekLabel(week)}</option>
                          {/each}
                        </select>
                      </div>
                    {/if}
                    <div>
                      <label
                        for="rec-plan-{i}-weekday"
                        class="mb-1 block text-xs font-medium text-text-muted"
                      >
                        {t("tasks.recurrence.anchor.weekday")}
                      </label>
                      <select id="rec-plan-{i}-weekday" bind:value={row.weekday} class={smallClass}>
                        {#each weekdays as name, index (name)}
                          <option value={String(index)}>{name}</option>
                        {/each}
                      </select>
                    </div>
                  {:else if row.placement === "day"}
                    <div>
                      <label
                        for="rec-plan-{i}-day"
                        class="mb-1 block text-xs font-medium text-text-muted"
                      >
                        {t("tasks.recurrence.anchor.day")}
                      </label>
                      <input
                        id="rec-plan-{i}-day"
                        type="number"
                        min="1"
                        max="31"
                        bind:value={row.day}
                        class="{smallClass} w-16"
                      />
                    </div>
                  {/if}
                  <div>
                    <label
                      for="rec-plan-{i}-start"
                      class="mb-1 block text-xs font-medium text-text-muted"
                    >
                      {t("tasks.recurrence.plan.start")}
                    </label>
                    <TimeInput
                      name="_plan_start_{i}"
                      id="rec-plan-{i}-start"
                      bind:value={row.start}
                    />
                  </div>
                  <div>
                    <label
                      for="rec-plan-{i}-duration"
                      class="mb-1 block text-xs font-medium text-text-muted"
                    >
                      {t("tasks.recurrence.plan.duration")}
                    </label>
                    <DurationInput id="rec-plan-{i}-duration" bind:minutes={row.minutes} />
                  </div>
                  {#if rows.length > 1}
                    <button
                      type="button"
                      class="mb-1 rounded-lg p-1.5 text-text-muted hover:bg-surface hover:text-red-600"
                      aria-label={t("tasks.recurrence.plan.remove_block")}
                      title={t("tasks.recurrence.plan.remove_block")}
                      onclick={() => removeRow(row.key)}
                    >
                      <X size={14} />
                    </button>
                  {/if}
                </div>
                <div
                  class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,14rem)]"
                >
                  <div>
                    <span class="mb-1 block text-xs font-medium text-text-muted">
                      {t("tasks.recurrence.plan.person")}
                    </span>
                    {#if canScheduleAny}
                      <!-- Chips, like Inplannen (docs/UX.md): a block is for however many people
                           share it; none named means everyone on the task, resolved when the
                           occurrence is created rather than frozen now. -->
                      <MembersPicker
                        name="_plan_people_{i}"
                        id="rec-plan-{i}-people"
                        bind:value={row.userIds}
                        {members}
                        placeholder={t("tasks.recurrence.plan.person_assignee")}
                      />
                    {:else}
                      <!-- `:own` plans only yourself, so the field states that rather than offering
                           a picker whose every other option the API would refuse. -->
                      <p class="rounded-lg border border-border px-3 py-2 text-sm text-text-muted">
                        {t("tasks.schedule.you")}
                      </p>
                    {/if}
                  </div>
                  <div>
                    <label
                      for="rec-plan-{i}-note"
                      class="mb-1 block text-xs font-medium text-text-muted"
                    >
                      {t("tasks.recurrence.plan.note")}
                    </label>
                    <input
                      id="rec-plan-{i}-note"
                      type="text"
                      maxlength="500"
                      bind:value={row.note}
                      class="{smallClass} w-full"
                    />
                  </div>
                </div>
              </li>
            {/each}
          </ul>
          <button
            type="button"
            class="mt-2 flex items-center gap-1 rounded-lg border border-dashed border-border px-2.5 py-1.5 text-xs text-text hover:border-brand hover:text-brand"
            onclick={addRow}
            disabled={rows.length >= 20}
          >
            <Plus size={14} />
            {t("tasks.recurrence.plan.add_block")}
          </button>
          <p class="mt-2 text-[11px] leading-snug text-text-muted">
            {t("tasks.recurrence.plan.day_hint")}
          </p>
          <!-- The blocks the next occurrence would book, each on the day its placement lands —
               under the rows that place them, for the same reason the date sits beside the
               cadence: an answer belongs next to the question. -->
          {#if !previewFailed && preview?.blocks.length}
            <ul
              class="mt-2 space-y-0.5 rounded-lg bg-surface px-3 py-2 text-xs"
              data-testid="plan-preview"
            >
              {#each preview.blocks as block, i (i)}
                <li
                  class="flex flex-wrap items-baseline gap-x-2 {block.in_past
                    ? 'text-text-muted line-through'
                    : 'text-text'}"
                >
                  <span class="font-medium">
                    {capitalizeFirst(fmtWeekdayShort(block.day))}
                    {fmtDayMonthYear(block.day)}
                  </span>
                  <span>{clockOf(block.start_time)}{RANGE_DASH}{clockOf(block.end_time)}</span>
                  <span class="text-text-muted">{previewPeople(block)}</span>
                  {#if block.in_past}
                    <span class="text-[11px] no-underline">
                      {t("tasks.recurrence.plan.in_past")}
                    </span>
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}
        {/if}
      </div>
    {/if}

    <!-- What travels and what does not, stated where the rule is written rather than discovered
         a month later when the briefing link is missing from the new occurrence. -->
    <details class="text-[11px] leading-snug text-text-muted">
      <summary class="cursor-pointer font-medium">{t("tasks.recurrence.carries.title")}</summary>
      <p class="mt-1">{t("tasks.recurrence.carries")}</p>
      <p class="mt-1">{t("tasks.recurrence.carries_not")}</p>
    </details>
  {/if}
</div>
