<script lang="ts">
  import { CircleCheck, Plus } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { fmtDayMonth, fmtLongDay, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";
  import EntryForm from "$lib/modules/time/EntryForm.svelte";
  import { formatMinutes, formatTime } from "$lib/modules/time/format";
  import TimesheetGrid from "$lib/modules/time/TimesheetGrid.svelte";
  import { page } from "$app/state";

  let { data, form } = $props();

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
  let showNewCompany = $state(false);
  let showNewProject = $state(false);
  let draftCompanyName = $state("");
  let draftProjectName = $state("");

  // --- "add hours" jump button ----------------------------------------------------
  let panelEl: HTMLElement | undefined = $state();
  function jumpToNewEntry() {
    editingId = null; // panel back to create mode
    requestAnimationFrame(() => {
      panelEl?.scrollIntoView({ behavior: "smooth", block: "start" });
      panelEl?.querySelector<HTMLInputElement>('input[name="start"]')?.focus();
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
  <title>{pageTitle(t("time.title"))}</title>
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
      <form method="POST" action="?/stopTimer" use:enhance>
        <button
          class="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          {t("time.timer.stop")}
        </button>
      </form>
    {:else}
      <form
        method="POST"
        action="?/startTimer"
        use:enhance
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
          />
        </div>
        <div class="w-40">
          <Combobox
            items={timerProjects}
            name="project_id"
            bind:value={timerProject}
            id="timer-project"
            placeholder={t("time.field.project")}
          />
        </div>
        <button
          class="flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          ▶ {t("time.timer.start")}
        </button>
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
        <span class="text-[11px] text-text-muted"
          >{week.day_totals[i] ? formatMinutes(week.day_totals[i]) : "·"}</span
        >
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
    class="h-fit scroll-mt-4 rounded-xl border border-border bg-surface-raised p-5"
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
          canSeeMoney={data.canSeeBudgetMoney}
          error={form?.error ?? null}
          oncancel={() => (editingId = null)}
          ondone={() => (editingId = null)}
          oncreatecompany={(name) => {
            draftCompanyName = name;
            showNewCompany = true;
          }}
          oncreateproject={(name) => {
            draftProjectName = name;
            showNewProject = true;
          }}
        />
      {/key}
    {:else}
      <h2 class="mb-4 text-sm font-semibold text-text">{t("time.new_registration")}</h2>
      {#key data.selectedDate}
        <EntryForm
          action="?/createEntry"
          date={data.selectedDate}
          companies={data.companies}
          projects={data.projects}
          tasks={data.tasks}
          canSeeMoney={data.canSeeBudgetMoney}
          defaultCompanyId={data.lastCompanyId ?? ""}
          defaultProjectId={data.lastProjectId ?? ""}
          error={form?.error ?? null}
          oncreatecompany={(name) => {
            draftCompanyName = name;
            showNewCompany = true;
          }}
          oncreateproject={(name) => {
            draftProjectName = name;
            showNewProject = true;
          }}
        />
      {/key}
    {/if}
  </aside>
</div>

<!-- Quick-create: a full new client/project without leaving the timesheet. Custom fields
     (incl. required ones) come from the tenant's definitions via the API. -->
<Modal bind:open={showNewCompany} title={t("time.quick_create.company")}>
  {#key draftCompanyName + String(showNewCompany)}
    <form
      method="POST"
      action="?/createCompany"
      use:enhance={() =>
        ({ update }) => {
          showNewCompany = false;
          void update();
        }}
      class="space-y-3"
    >
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
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showNewCompany = false)}>{t("common.cancel")}</button
        >
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("common.create")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<Modal bind:open={showNewProject} title={t("time.quick_create.project")}>
  {#key draftProjectName + String(showNewProject)}
    <form
      method="POST"
      action="?/createProject"
      use:enhance={() =>
        ({ update }) => {
          showNewProject = false;
          void update();
        }}
      class="space-y-3"
    >
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
            id="qc-project-company"
            placeholder={t("time.field.company")}
          />
        </div>
        <div>
          <label for="qc-project-rate" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.hourly_rate")}</label
          >
          <input
            id="qc-project-rate"
            name="hourly_rate"
            type="number"
            min="0"
            step="0.01"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
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
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showNewProject = false)}>{t("common.cancel")}</button
        >
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("common.create")}</button
        >
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
