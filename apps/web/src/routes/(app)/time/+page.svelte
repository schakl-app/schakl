<script lang="ts">
  import { CircleCheck, Plus, Sparkles } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { aiEnabled } from "$lib/core/ai";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { fmtDayMonth, fmtLongDay, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";
  import EntryForm from "$lib/modules/time/EntryForm.svelte";
  import { formatMinutes, formatTime } from "$lib/modules/time/format";
  import TimesheetGrid from "$lib/modules/time/TimesheetGrid.svelte";
  import { page } from "$app/state";

  let { data, form } = $props();

  const busy = new InFlight();

  // Approved hours are signed off; only whoever may approve them may still change them.
  const canApprove = $derived(can(page.data.user, "time.entry.approve"));

  // Personal timesheet view (7-day vs Mon–Fri); day tabs mirror the grid.
  const weekView = $derived<"full" | "work">(data.weekView === "work" ? "work" : "full");
  let viewForm: HTMLFormElement | undefined = $state();

  // --- lookups (from the /time layout load) ----------------------------------
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const taskTitle = (id?: string | null) => data.tasks.find((tk) => tk.id === id)?.title ?? "";

  function entryLabel(e: {
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
  }) {
    const parts = [
      companyName(e.company_id),
      projectName(e.project_id),
      taskTitle(e.task_id),
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("time.general");
  }

  // --- week / day navigation --------------------------------------------------
  function shiftWeek(iso: string, deltaDays: number): string {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + deltaDays);
    return d.toISOString().slice(0, 10);
  }
  /** ISO date of the Monday on or before `iso` (mirrors the server helper). */
  function weekStartOf(iso: string): string {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  // Jump straight to any date/week via the (low-key) date picker.
  function jumpToDate(iso: string) {
    if (iso) void goto(`?date=${iso}&week=${weekStartOf(iso)}`, { keepFocus: true });
  }
  const dayNum = (iso: string) => Number(iso.slice(8, 10));

  const week = $derived(data.week);
  // Mon–Fri only in workweek view; the day tabs mirror the grid.
  const visibleDays = $derived(
    week ? (weekView === "work" ? week.days.slice(0, 5) : week.days) : [],
  );
  const lastVisibleDay = $derived(week ? week.days[weekView === "work" ? 4 : 6] : "");
  const entries = $derived(
    [...(data.day?.entries ?? [])].sort((a, b) => a.started_at.localeCompare(b.started_at)),
  );

  // --- edit-in-panel state ------------------------------------------------------
  let editingId = $state<string | null>(null);
  const editingEntry = $derived(entries.find((e) => e.id === editingId) ?? null);
  // Leaving the day (or the entry disappearing) resets the panel to create mode.
  $effect(() => {
    if (editingId && !editingEntry) editingId = null;
  });
  function rowClick(e: (typeof entries)[number]) {
    if (e.is_running) return;
    if (e.approved_at && !canApprove) return; // approved hours are locked
    editingId = editingId === e.id ? null : e.id;
  }

  // --- quick-create dialogs (opened by typing an unknown name in a picker) --------
  // The slot names the picker that asked, so `inlineCreated` auto-selects only there
  // (docs/UX.md) — a client created from the timer never lands in the entry form's picker.
  let showNewCompany = $state(false);
  let showNewProject = $state(false);
  let draftCompanyName = $state("");
  let draftProjectName = $state("");
  let companySlot = $state("entry_company");
  let projectSlot = $state("entry_project");
  // The project modal's own client picker; bound so a stacked company create selects here.
  let qcProjectCompany = $state("");

  function quickCreateCompany(name: string, slot: string) {
    draftCompanyName = name;
    companySlot = slot;
    showNewCompany = true;
  }
  function quickCreateProject(name: string, slot: string) {
    draftProjectName = name;
    projectSlot = slot;
    qcProjectCompany = "";
    showNewProject = true;
  }

  // --- "add hours" jump button ----------------------------------------------------
  let panelEl: HTMLElement | undefined = $state();
  function jumpToNewEntry() {
    editingId = null; // panel back to create mode
    requestAnimationFrame(() => {
      panelEl?.scrollIntoView({ behavior: "smooth", block: "start" });
      panelEl?.querySelector<HTMLInputElement>('input[name="start"]')?.focus();
    });
  }

  // --- AI time assist (#129): quick add + day reconstruction --------------------
  // Every AI product here is a *draft*: parse and suggestions only prefill the create form;
  // a real entry exists only after the user saves through the normal action.
  const hasTimeAssist = $derived(aiEnabled(page.data.user, "time_assist"));
  let aiText = $state("");
  let aiBusy = $state(false);
  let aiError = $state<string | null>(null);
  let aiBudget = $state(false);
  let aiPrefill = $state<Record<string, unknown> | null>(null);
  let aiPrefillVersion = $state(0);
  // The parse never saves anything (#129) — so say loudly what it *did* do: a summary line
  // under the input and a flash on the form panel, or an honest "couldn't parse".
  let aiParsedSummary = $state<string | null>(null);
  let aiFlash = $state(false);

  interface AISuggestion {
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
    minutes?: number | null;
    description: string;
    label: string;
  }
  interface AIRecon {
    short: boolean;
    scheduled_minutes: number;
    logged_minutes: number;
    leave_minutes: number;
    suggestions: AISuggestion[];
  }
  let recon = $state<{ loading: boolean; data?: AIRecon; error?: string } | null>(null);

  // A new day gets a clean slate — suggestions and prefills belong to the day they were
  // made for. Guarded by value: an unrelated invalidation (a save, a draft write) replaces
  // `data` without changing the day and must not wipe a prefill mid-flight.
  let lastResetDay: string | null = null;
  $effect(() => {
    const day = data.selectedDate;
    if (lastResetDay !== null && day !== lastResetDay) {
      recon = null;
      aiPrefill = null;
      aiParsedSummary = null;
    }
    lastResetDay = day;
  });

  function endFrom(start: string, minutes: number): string {
    const [h, m] = start.split(":").map(Number);
    const total = (h * 60 + m + minutes) % (24 * 60);
    return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }

  function openPrefilled(prefill: Record<string, unknown>) {
    aiPrefill = prefill;
    aiPrefillVersion++;
    aiFlash = true;
    setTimeout(() => (aiFlash = false), 2500);
    jumpToNewEntry();
  }

  async function aiQuickAdd(override = false) {
    if (!aiText.trim() || aiBusy) return;
    aiBusy = true;
    aiError = null;
    aiBudget = false;
    aiParsedSummary = null;
    try {
      const res = await fetch("/ai/time/parse", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: aiText, override_budget: override }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        if (payload?.error?.code === "ai_budget_reached") aiBudget = true;
        else aiError = payload?.error?.message ?? "errors.ai_provider_error";
        return;
      }
      const parsed = await res.json();
      const useful =
        parsed.date ||
        parsed.start ||
        parsed.duration_minutes ||
        parsed.company_id ||
        parsed.project_id ||
        parsed.task_id ||
        parsed.description;
      if (!useful) {
        // Ambiguity stays visible (#129): keep the text so the user can refine it.
        aiError = "ai.time.parse_empty";
        return;
      }
      let start: string = parsed.start ?? "";
      let end: string = parsed.end ?? "";
      if (start && !end && parsed.duration_minutes) end = endFrom(start, parsed.duration_minutes);
      if (!start && parsed.duration_minutes) {
        start = "09:00";
        end = endFrom(start, parsed.duration_minutes);
      }
      // "gisteren 2 uur …" belongs on yesterday: switch the view to the parsed day first,
      // so the prefilled form — and after Opslaan the entry itself — appear on the day the
      // user is looking at, never invisibly on another one.
      const targetDate: string = parsed.date ?? data.selectedDate;
      if (targetDate !== data.selectedDate) {
        await goto(`?date=${targetDate}&week=${weekStartOf(targetDate)}`, { keepFocus: true });
      }
      openPrefilled({
        date: targetDate,
        start,
        end,
        company_id: parsed.company_id ?? "",
        project_id: parsed.project_id ?? "",
        task_id: parsed.task_id ?? "",
        description: parsed.description ?? "",
      });
      const pieces: string[] = [];
      if (parsed.date) pieces.push(fmtDayMonth(parsed.date));
      if (start && end) pieces.push(`${start}–${end}`);
      else if (parsed.duration_minutes) pieces.push(formatMinutes(parsed.duration_minutes));
      for (const name of [
        companyName(parsed.company_id),
        projectName(parsed.project_id),
        taskTitle(parsed.task_id),
      ]) {
        if (name) pieces.push(name);
      }
      if (parsed.description) pieces.push(`"${parsed.description}"`);
      aiParsedSummary = pieces.join(" · ");
      aiText = "";
    } catch {
      aiError = "errors.ai_provider_error";
    } finally {
      aiBusy = false;
    }
  }

  async function reconstruct(override = false) {
    recon = { loading: true };
    aiBudget = false;
    try {
      const res = await fetch("/ai/time/reconstruct", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ date: data.selectedDate, override_budget: override }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        if (payload?.error?.code === "ai_budget_reached") {
          recon = null;
          aiBudget = true;
        } else {
          recon = { loading: false, error: payload?.error?.message ?? "errors.ai_provider_error" };
        }
        return;
      }
      recon = { loading: false, data: await res.json() };
    } catch {
      recon = { loading: false, error: "errors.ai_provider_error" };
    }
  }

  function applySuggestion(suggestion: AISuggestion) {
    let start = "";
    let end = "";
    if (suggestion.minutes) {
      start = "09:00";
      end = endFrom(start, suggestion.minutes);
    }
    openPrefilled({
      date: data.selectedDate,
      start,
      end,
      company_id: suggestion.company_id ?? "",
      project_id: suggestion.project_id ?? "",
      task_id: suggestion.task_id ?? "",
      description: suggestion.description,
    });
  }

  // --- timer start form --------------------------------------------------------
  let timerCompany = $state("");
  let timerProject = $state("");
  const timerProjects = $derived(
    (timerCompany
      ? data.projects.filter((p) => p.company_id === timerCompany || !p.company_id)
      : data.projects
    ).map((p) => ({ value: p.id, label: p.name })),
  );

  // A quick-create answers with `inlineCreated` (server create → auto-select): only the
  // slot that asked gets the new id. The entry form's pickers keep their own wiring.
  $effect(() => {
    const created = form?.inlineCreated;
    if (!created?.id) return;
    if (created.slot === "timer_company") timerCompany = created.id;
    if (created.slot === "project_company") qcProjectCompany = created.id;
    if (created.slot === "timer_project") {
      timerProject = created.id;
      // Back-fill the client like picking one would: the new project may belong to a
      // client the timer hadn't picked, and would otherwise be filtered out of the list.
      if ("company_id" in created && created.company_id) timerCompany = created.company_id;
    }
  });

  // --- live running-timer clock ---------------------------------------------
  let nowMs = $state(Date.now());
  $effect(() => {
    if (!data.running) return;
    const id = setInterval(() => (nowMs = Date.now()), 1000);
    return () => clearInterval(id);
  });
  function elapsed(startIso: string): string {
    const s = Math.max(0, Math.floor((nowMs - new Date(startIso).getTime()) / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  const dayHref = (iso: string) => `?date=${iso}&week=${data.week_start}`;
</script>

<svelte:head>
  <title>{pageTitle(navLabel("time", t("time.title")))}</title>
</svelte:head>

<!-- Top bar: week navigation + running timer -->
<div
  class="mb-4 flex flex-wrap items-center justify-between gap-3"
  data-sveltekit-preload-data="hover"
>
  <!-- Wraps like its parent (issue #36): six controls, a `w-32` date field and a select add up to
       a min-content width of ~468px, so a phone laid the whole shell out that wide. -->
  <div class="flex flex-wrap items-center gap-2 text-sm">
    <a
      href={`?week=${shiftWeek(data.week_start, -7)}&date=${shiftWeek(data.selectedDate, -7)}`}
      class="rounded-lg border border-border px-2 py-1 hover:bg-surface"
      aria-label="←">←</a
    >
    <span class="font-medium text-text">
      {week ? `${fmtDayMonth(week.days[0])} – ${fmtDayMonth(lastVisibleDay)}` : ""}
    </span>
    <a
      href={`?week=${shiftWeek(data.week_start, 7)}&date=${shiftWeek(data.selectedDate, 7)}`}
      class="rounded-lg border border-border px-2 py-1 hover:bg-surface"
      aria-label="→">→</a
    >
    <a href={dayHref(data.today)} class="ml-1 rounded-lg px-2 py-1 text-text-muted hover:text-text">
      {t("time.today_badge")}
    </a>
    <!-- Low-key: jump to a specific date/week, and choose the week view. -->
    <div class="w-32">
      <DateInput name="_jump" id="jump-date" value={data.selectedDate} onchange={jumpToDate} />
    </div>
    <form method="POST" action="?/saveView" use:enhance bind:this={viewForm}>
      <select
        name="week_view"
        aria-label={t("time.view.label")}
        onchange={() => viewForm?.requestSubmit()}
        class="rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:text-text"
      >
        <option value="full" selected={weekView === "full"}>{t("time.view.full_week")}</option>
        <option value="work" selected={weekView === "work"}>{t("time.view.work_week")}</option>
      </select>
    </form>
  </div>

  <div class="flex items-center gap-3">
    {#if data.running}
      <div class="flex items-center gap-2">
        <span class="h-2.5 w-2.5 animate-pulse rounded-full bg-green-500"></span>
        <span class="font-mono text-sm tabular-nums text-text"
          >{elapsed(data.running.started_at)}</span
        >
        <span class="max-w-[16rem] truncate text-sm text-text-muted">
          {data.running.description || entryLabel(data.running)}
        </span>
      </div>
      <form method="POST" action="?/stopTimer" use:enhance={busy.wrap("stopTimer")}>
        <Button variant="danger" size="sm" loading={busy.is("stopTimer")} disabled={busy.active}>
          {t("time.timer.stop")}
        </Button>
      </form>
    {:else}
      <form
        method="POST"
        action="?/startTimer"
        use:enhance={busy.wrap("startTimer")}
        class="flex flex-wrap items-center gap-2"
      >
        <input
          name="description"
          placeholder={t("time.field.description")}
          class="w-40 rounded-lg border border-border px-2 py-1.5 text-sm"
        />
        <div class="w-40">
          <Combobox
            items={data.companies.map((c) => ({ value: c.id, label: c.name }))}
            name="company_id"
            bind:value={timerCompany}
            id="timer-company"
            placeholder={t("time.field.company")}
            oncreate={(name) => quickCreateCompany(name, "timer_company")}
          />
        </div>
        <div class="w-40">
          <Combobox
            items={timerProjects}
            name="project_id"
            bind:value={timerProject}
            id="timer-project"
            placeholder={t("time.field.project")}
            oncreate={(name) => quickCreateProject(name, "timer_project")}
          />
        </div>
        <Button size="sm" loading={busy.is("startTimer")} disabled={busy.active}>
          ▶ {t("time.timer.start")}
        </Button>
      </form>
    {/if}
  </div>
</div>

<!-- Day tabs -->
{#if week}
  <div
    class="mb-4 grid overflow-hidden rounded-xl border border-border bg-surface-raised {weekView ===
    'work'
      ? 'grid-cols-5'
      : 'grid-cols-7'}"
    data-sveltekit-preload-data="hover"
  >
    {#each visibleDays as day, i (day)}
      {@const sel = day === data.selectedDate}
      <a
        href={dayHref(day)}
        class="flex flex-col items-center gap-0.5 border-r border-border px-1 py-2 text-center last:border-r-0 hover:bg-surface
          {sel ? 'bg-brand/5' : ''}"
      >
        <span class="text-[11px] uppercase {sel ? 'text-brand' : 'text-text-muted'}"
          >{fmtWeekdayShort(day)}</span
        >
        <span class="text-sm font-semibold {sel ? 'text-brand' : 'text-text'}">{dayNum(day)}</span>
        <span class="inline-flex items-center gap-1 text-[11px] text-text-muted">
          {week.day_totals[i] ? formatMinutes(week.day_totals[i]) : "·"}
          {#if week.draft_days?.includes(day)}
            <!-- An unsaved draft lives on this day (#44). -->
            <span class="h-1.5 w-1.5 rounded-full bg-amber-400" title={t("time.draft.chip")}></span>
          {/if}
        </span>
      </a>
    {/each}
  </div>
{/if}

<div class="grid min-w-0 gap-4 lg:grid-cols-[1fr_360px]">
  <!-- Selected day: entries -->
  <main class="min-w-0 rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h2 class="text-base font-semibold capitalize text-text">
          {fmtLongDay(data.selectedDate)}
        </h2>
        {#if data.selectedDate === data.today}
          <span
            class="mt-1 inline-block rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
            >{t("time.today_badge")}</span
          >
        {/if}
      </div>
      <div class="flex items-center gap-2">
        {#if data.day && data.day.total_minutes > 0}
          <span class="text-sm font-medium text-text">{formatMinutes(data.day.total_minutes)}</span>
        {/if}
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-text-muted hover:border-brand hover:text-brand"
          onclick={jumpToNewEntry}
          aria-label={t("time.add_hours")}
          title={t("time.add_hours")}
        >
          <Plus size={15} />
        </button>
      </div>
    </div>

    {#if hasTimeAssist}
      <!-- AI quick add (#129): parse prefills the form for one confirming glance; the day
           reconstruction runs on demand so it suggests, never nags. -->
      <div class="mb-4 space-y-2">
        <form
          class="flex flex-wrap gap-2"
          onsubmit={(e) => {
            e.preventDefault();
            void aiQuickAdd();
          }}
        >
          <input
            bind:value={aiText}
            placeholder={t("ai.time.quick_add_placeholder")}
            class="min-w-0 flex-1 rounded-lg border border-border bg-transparent px-3 py-2 text-sm text-text outline-none focus:border-brand"
            aria-label={t("ai.time.quick_add")}
          />
          <button
            type="submit"
            disabled={aiBusy || !aiText.trim()}
            class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-text hover:border-brand disabled:opacity-40"
          >
            <Sparkles size={14} class={aiBusy ? "animate-pulse" : ""} />
            {t("ai.time.quick_add")}
          </button>
          {#if data.selectedDate <= data.today}
            <button
              type="button"
              disabled={recon?.loading}
              onclick={() => void reconstruct()}
              class="rounded-lg border border-border px-3 py-2 text-sm text-text-muted hover:border-brand hover:text-text disabled:opacity-40"
              title={t("ai.time.reconstruct_hint")}
            >
              {recon?.loading ? t("ai.time.reconstructing") : t("ai.time.reconstruct")}
            </button>
          {/if}
        </form>
        {#if aiParsedSummary}
          <button
            type="button"
            class="block text-left text-sm text-green-700 hover:underline dark:text-green-400"
            onclick={jumpToNewEntry}
          >
            {t("ai.time.parsed", { summary: aiParsedSummary })}
          </button>
        {/if}
        {#if aiError}<p class="text-sm text-red-600 dark:text-red-400">{t(aiError)}</p>{/if}
        {#if aiBudget}
          <p class="text-sm text-amber-700 dark:text-amber-400">
            {t("ai.budget_notice")}
            <button type="button" class="underline" onclick={() => void aiQuickAdd(true)}
              >{t("ai.budget_proceed")}</button
            >
          </p>
        {/if}
        {#if recon && !recon.loading}
          {#if recon.error}
            <p class="text-sm text-red-600 dark:text-red-400">{t(recon.error)}</p>
          {:else if recon.data && !recon.data.short}
            <p class="text-sm text-text-muted">{t("ai.time.day_complete")}</p>
          {:else if recon.data}
            <div class="rounded-lg border border-dashed border-border p-3">
              <p class="mb-2 text-xs text-text-muted">
                {t("ai.time.missing", {
                  missing: formatMinutes(
                    Math.max(
                      0,
                      recon.data.scheduled_minutes -
                        recon.data.leave_minutes -
                        recon.data.logged_minutes,
                    ),
                  ),
                })}
              </p>
              {#if recon.data.suggestions.length === 0}
                <p class="text-sm text-text-muted">{t("ai.time.no_suggestions")}</p>
              {:else}
                <div class="flex flex-wrap gap-2">
                  {#each recon.data.suggestions as suggestion, i (i)}
                    <button
                      type="button"
                      class="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-text hover:border-brand"
                      onclick={() => applySuggestion(suggestion)}
                      title={suggestion.description}
                    >
                      <Sparkles size={11} class="text-text-muted" />
                      {suggestion.label}
                      {#if suggestion.minutes}<span class="text-text-muted"
                          >{formatMinutes(suggestion.minutes)}</span
                        >{/if}
                    </button>
                  {/each}
                </div>
              {/if}
              <button
                type="button"
                class="mt-2 text-xs text-text-muted hover:text-text"
                onclick={() => (recon = null)}>{t("common.close")}</button
              >
            </div>
          {/if}
        {/if}
      </div>
    {/if}

    {#if entries.length === 0}
      <div class="rounded-xl border border-dashed border-border p-10 text-center">
        <p class="font-medium text-text">{t("time.day_empty")}</p>
        <p class="mt-1 text-sm text-text-muted">{t("time.day_empty_hint")}</p>
      </div>
    {:else}
      <ul class="space-y-2">
        {#each entries as e (e.id)}
          {@const locked = Boolean(e.approved_at) && !canApprove}
          <li>
            <!-- On a phone the row can't fit time + label + billable pill + total on one line
                 (issue #84): it `flex-wrap`s, and the meta cluster (approved/break/billable/
                 total) drops to its own full-width line via `w-full sm:w-auto` instead of
                 overflowing the page. Desktop keeps everything inline, order unchanged. -->
            <button
              type="button"
              class="flex w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border p-3 text-left
                {editingId === e.id ? 'border-brand ring-1 ring-brand' : 'border-border'}
                {locked || e.is_running
                ? 'cursor-default'
                : 'hover:border-brand/60 hover:bg-surface'}"
              onclick={() => rowClick(e)}
              title={locked ? t("time.approved_locked_hint") : undefined}
              aria-expanded={editingId === e.id}
            >
              <div class="w-24 shrink-0 text-sm text-text-muted">
                {#if e.is_running}
                  <span class="text-green-600 dark:text-green-400">● {t("time.timer.running")}</span
                  >
                {:else}
                  {formatTime(e.started_at)}–{formatTime(e.ended_at)}
                {/if}
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-text">{entryLabel(e)}</p>
                {#if e.description}<p class="truncate text-xs text-text-muted">
                    {e.description}
                  </p>{/if}
              </div>
              <div
                class="flex w-full flex-wrap items-center justify-end gap-x-3 gap-y-1 sm:w-auto sm:flex-nowrap"
              >
                {#if e.approved_at}
                  <span
                    title={t("time.approved")}
                    class="shrink-0 text-green-600 dark:text-green-400"
                  >
                    <CircleCheck size={16} />
                  </span>
                {/if}
                {#if e.break_minutes > 0}
                  <span class="shrink-0 text-xs text-text-muted"
                    >{t("time.break_short", { minutes: e.break_minutes })}</span
                  >
                {/if}
                <span
                  class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium
                  {e.billable
                    ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
                    : 'bg-surface text-text-muted'}"
                >
                  {e.billable ? t("time.billable") : t("time.not_billable")}
                </span>
                <span class="w-16 shrink-0 text-right text-sm font-semibold tabular-nums text-text"
                  >{formatMinutes(e.minutes)}</span
                >
              </div>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </main>

  <!-- New registration / edit panel -->
  <aside
    bind:this={panelEl}
    class="h-fit scroll-mt-4 rounded-xl border border-border bg-surface-raised p-5 transition-shadow duration-500 {aiFlash
      ? 'ring-2 ring-brand'
      : ''}"
  >
    {#if editingEntry}
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-text">{t("time.edit_entry")}</h2>
        {#if editingEntry.approved_at}
          <span
            class="flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400"
          >
            <CircleCheck size={14} />
            {t("time.approved")}
          </span>
        {/if}
      </div>
      {#key editingEntry.id}
        <EntryForm
          action="?/updateEntry"
          deleteAction="?/deleteEntry"
          entry={editingEntry}
          date={data.selectedDate}
          companies={data.companies}
          projects={data.projects}
          tasks={data.tasks}
          subscriptions={data.subscriptions}
          error={form?.error ?? null}
          oncancel={() => (editingId = null)}
          ondone={() => (editingId = null)}
          oncreatecompany={(name) => quickCreateCompany(name, "entry_company")}
          oncreateproject={(name) => quickCreateProject(name, "entry_project")}
        />
      {/key}
    {:else}
      <h2 class="mb-4 text-sm font-semibold text-text">{t("time.new_registration")}</h2>
      {#key `${data.selectedDate}:${aiPrefillVersion}`}
        <EntryForm
          action="?/createEntry"
          date={data.selectedDate}
          companies={data.companies}
          projects={data.projects}
          tasks={data.tasks}
          subscriptions={data.subscriptions}
          draftDate={data.selectedDate}
          draftInitial={aiPrefill ?? data.day?.draft?.payload ?? null}
          draftSavedAt={aiPrefill ? null : (data.day?.draft?.updated_at ?? null)}
          defaultCompanyId={aiPrefill ? "" : data.presetCompanyId || (data.lastCompanyId ?? "")}
          defaultProjectId={aiPrefill || data.presetCompanyId ? "" : (data.lastProjectId ?? "")}
          error={form?.error ?? null}
          ondone={() => {
            aiPrefill = null;
            aiParsedSummary = null;
          }}
          oncreatecompany={(name) => quickCreateCompany(name, "entry_company")}
          oncreateproject={(name) => quickCreateProject(name, "entry_project")}
        />
      {/key}
    {/if}
  </aside>
</div>

<!-- Quick-create: a full new client/project without leaving the timesheet. Custom fields
     (incl. required ones) come from the tenant's definitions via the API. The company modal
     renders last so it stacks *above* the project modal, whose own client picker can open it. -->
<Modal bind:open={showNewProject} title={t("time.quick_create.project")}>
  {#key draftProjectName + String(showNewProject)}
    <form
      method="POST"
      action="?/createProject"
      use:enhance={busy.wrap("qcProject", () => ({ result, update }) => {
        if (result.type === "success") showNewProject = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={projectSlot} />
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-project-name" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.name")}</label
          >
          <input
            id="qc-project-name"
            name="name"
            value={draftProjectName}
            required
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="qc-project-company" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.company")}</label
          >
          <Combobox
            items={data.companies.map((c) => ({ value: c.id, label: c.name }))}
            name="company_id"
            bind:value={qcProjectCompany}
            id="qc-project-company"
            placeholder={t("time.field.company")}
            oncreate={(name) => quickCreateCompany(name, "project_company")}
          />
        </div>
        <div class="flex items-center gap-2 pt-6">
          <input
            id="qc-project-billable"
            name="billable_default"
            type="checkbox"
            checked
            class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
          />
          <label for="qc-project-billable" class="text-sm font-medium text-text"
            >{t("projects.field.billable_default")}</label
          >
        </div>
      </div>
      {#if data.projectDefinitions.length > 0}
        <CustomFieldsForm definitions={data.projectDefinitions} locale={data.locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if form?.qcError}<p class="text-sm text-red-600 dark:text-red-400">
          {t(form.qcError)}
        </p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showNewProject = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcProject")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<Modal bind:open={showNewCompany} title={t("time.quick_create.company")}>
  {#key draftCompanyName + String(showNewCompany)}
    <form
      method="POST"
      action="?/createCompany"
      use:enhance={busy.wrap("qcCompany", () => ({ result, update }) => {
        if (result.type === "success") showNewCompany = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={companySlot} />
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-company-name" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.name")}</label
          >
          <input
            id="qc-company-name"
            name="name"
            value={draftCompanyName}
            required
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="qc-company-status" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.field.status")}</label
          >
          <select
            id="qc-company-status"
            name="status"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm"
          >
            {#each COMPANY_STATUSES as status (status)}
              <option value={status} selected={status === "active"}
                >{t(`companies.status.${status}`)}</option
              >
            {/each}
          </select>
        </div>
        <div class="sm:col-span-2">
          <label for="qc-company-website" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.website")}</label
          >
          <input
            id="qc-company-website"
            name="website"
            placeholder="https://…"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
      </div>
      {#if data.companyDefinitions.length > 0}
        <CustomFieldsForm definitions={data.companyDefinitions} locale={data.locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if form?.qcError}<p class="text-sm text-red-600 dark:text-red-400">
          {t(form.qcError)}
        </p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showNewCompany = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcCompany")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Floating "add hours" button -->
<button
  type="button"
  class="fixed bottom-6 right-6 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-brand text-white shadow-lg hover:opacity-90"
  onclick={jumpToNewEntry}
  aria-label={t("time.add_hours")}
  title={t("time.add_hours")}
>
  <Plus size={22} />
</button>

<!-- Weekly grid -->
{#if week}
  <div class="mt-4">
    <TimesheetGrid
      {week}
      {weekView}
      companies={data.companies}
      projects={data.projects}
      tasks={data.tasks}
      leaveHours={data.leaveHours}
      holidays={data.holidays}
    />
  </div>
{/if}
