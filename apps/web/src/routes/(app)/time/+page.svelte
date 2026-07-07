<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { formatMinutes, formatTime } from "$lib/modules/time/format";

  let { data, form } = $props();

  // --- lookups --------------------------------------------------------------
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const taskTitle = (id?: string | null) => data.tasks.find((tk) => tk.id === id)?.title ?? "";

  function entryLabel(e: {
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
  }) {
    const parts = [companyName(e.company_id), projectName(e.project_id), taskTitle(e.task_id)].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("time.general");
  }

  // --- week / day navigation (dates shown in UTC to match stored wall-clock) -
  function shiftWeek(iso: string, deltaDays: number): string {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + deltaDays);
    return d.toISOString().slice(0, 10);
  }
  const tabFmt = new Intl.DateTimeFormat("nl-NL", { weekday: "short", timeZone: "UTC" });
  const rangeFmt = new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short", timeZone: "UTC" });
  const longFmt = new Intl.DateTimeFormat("nl-NL", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
  const d = (iso: string) => new Date(iso + "T00:00:00Z");
  const dayNum = (iso: string) => Number(iso.slice(8, 10));

  const week = $derived(data.week);
  const entries = $derived(
    [...(data.day?.entries ?? [])].sort((a, b) => a.started_at.localeCompare(b.started_at)),
  );

  // --- new-registration form state ------------------------------------------
  let fCompany = $state("");
  let fBillable = $state(true);
  const formProjects = $derived(
    fCompany ? data.projects.filter((p) => p.company_id === fCompany || !p.company_id) : data.projects,
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

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const dayHref = (iso: string) => `?date=${iso}&week=${data.week_start}`;
</script>

<svelte:head>
  <title>{t("time.title")}</title>
</svelte:head>

<!-- Top bar: week navigation + running timer -->
<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-2 text-sm">
    <a href={`?week=${shiftWeek(data.week_start, -7)}&date=${shiftWeek(data.selectedDate, -7)}`}
      class="rounded-lg border border-neutral-300 px-2 py-1 hover:bg-neutral-50" aria-label="←">←</a>
    <span class="font-medium text-neutral-800">
      {week ? `${rangeFmt.format(d(week.days[0]))} – ${rangeFmt.format(d(week.days[6]))}` : ""}
    </span>
    <a href={`?week=${shiftWeek(data.week_start, 7)}&date=${shiftWeek(data.selectedDate, 7)}`}
      class="rounded-lg border border-neutral-300 px-2 py-1 hover:bg-neutral-50" aria-label="→">→</a>
    <a href={dayHref(data.today)} class="ml-1 rounded-lg px-2 py-1 text-neutral-500 hover:text-neutral-900">
      {t("time.today_badge")}
    </a>
  </div>

  <div class="flex items-center gap-3">
    {#if data.running}
      <div class="flex items-center gap-2">
        <span class="h-2.5 w-2.5 animate-pulse rounded-full bg-green-500"></span>
        <span class="font-mono text-sm tabular-nums text-neutral-800">{elapsed(data.running.started_at)}</span>
        <span class="max-w-[16rem] truncate text-sm text-neutral-500">
          {data.running.description || entryLabel(data.running)}
        </span>
      </div>
      <form method="POST" action="?/stopTimer" use:enhance>
        <button class="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
          {t("time.timer.stop")}
        </button>
      </form>
    {:else}
      <form method="POST" action="?/startTimer" use:enhance class="flex items-center gap-2">
        <input name="description" placeholder={t("time.field.description")} class="w-40 rounded-lg border border-neutral-300 px-2 py-1.5 text-sm" />
        <button class="flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
          ▶ {t("time.timer.start")}
        </button>
      </form>
    {/if}
  </div>
</div>

<!-- Day tabs -->
{#if week}
  <div class="mb-4 grid grid-cols-7 overflow-hidden rounded-xl border border-neutral-200 bg-white">
    {#each week.days as day, i (day)}
      {@const sel = day === data.selectedDate}
      <a href={dayHref(day)}
        class="flex flex-col items-center gap-0.5 border-r border-neutral-100 px-1 py-2 text-center last:border-r-0 hover:bg-neutral-50
          {sel ? 'bg-brand/5' : ''}">
        <span class="text-[11px] uppercase {sel ? 'text-brand' : 'text-neutral-400'}">{tabFmt.format(d(day))}</span>
        <span class="text-sm font-semibold {sel ? 'text-brand' : 'text-neutral-800'}">{dayNum(day)}</span>
        <span class="text-[11px] text-neutral-400">{week.day_totals[i] ? formatMinutes(week.day_totals[i]) : "·"}</span>
      </a>
    {/each}
  </div>
{/if}

<div class="grid gap-4 lg:grid-cols-[1fr_360px]">
  <!-- Selected day: entries -->
  <main class="rounded-xl border border-neutral-200 bg-white p-5">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h2 class="text-base font-semibold capitalize text-neutral-900">{longFmt.format(d(data.selectedDate))}</h2>
        {#if data.selectedDate === data.today}
          <span class="mt-1 inline-block rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand">{t("time.today_badge")}</span>
        {/if}
      </div>
      {#if data.day && data.day.total_minutes > 0}
        <span class="text-sm font-medium text-neutral-700">{formatMinutes(data.day.total_minutes)}</span>
      {/if}
    </div>

    {#if entries.length === 0}
      <div class="rounded-xl border border-dashed border-neutral-300 p-10 text-center">
        <p class="font-medium text-neutral-900">{t("time.day_empty")}</p>
        <p class="mt-1 text-sm text-neutral-500">{t("time.day_empty_hint")}</p>
      </div>
    {:else}
      <ul class="space-y-2">
        {#each entries as e (e.id)}
          <li class="flex items-center gap-3 rounded-lg border border-neutral-200 p-3">
            <div class="w-24 shrink-0 text-sm text-neutral-500">
              {#if e.is_running}
                <span class="text-green-600">● {t("time.timer.running")}</span>
              {:else}
                {formatTime(e.started_at)}–{formatTime(e.ended_at)}
              {/if}
            </div>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium text-neutral-900">{entryLabel(e)}</p>
              {#if e.description}<p class="truncate text-xs text-neutral-500">{e.description}</p>{/if}
            </div>
            {#if e.break_minutes > 0}
              <span class="shrink-0 text-xs text-neutral-400">{t("time.break_short", { minutes: e.break_minutes })}</span>
            {/if}
            <span class="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium
              {e.billable ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500'}">
              {e.billable ? t("time.billable") : t("time.not_billable")}
            </span>
            <span class="w-16 shrink-0 text-right text-sm font-semibold tabular-nums text-neutral-900">{formatMinutes(e.minutes)}</span>
            <form method="POST" action="?/deleteEntry" use:enhance>
              <input type="hidden" name="id" value={e.id} />
              <button class="text-xs text-neutral-400 hover:text-red-600" aria-label={t("common.delete")}>✕</button>
            </form>
          </li>
        {/each}
      </ul>
    {/if}
  </main>

  <!-- New registration -->
  <aside class="rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("time.new_registration")}</h2>
    <form method="POST" action="?/createEntry" use:enhance class="space-y-3">
      <div class="grid grid-cols-3 gap-2">
        <div>
          <label for="start" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.start")}</label>
          <input id="start" name="start" type="time" required class={inputClass} />
        </div>
        <div>
          <label for="end" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.end")}</label>
          <input id="end" name="end" type="time" required class={inputClass} />
        </div>
        <div>
          <label for="break_minutes" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.break")}</label>
          <input id="break_minutes" name="break_minutes" type="number" min="0" step="5" value="0" class={inputClass} />
        </div>
      </div>

      <!-- billable segmented toggle -->
      <input type="hidden" name="billable" value={fBillable} />
      <div class="grid grid-cols-2 gap-2">
        <button type="button" onclick={() => (fBillable = false)}
          class="rounded-lg border px-3 py-2 text-sm font-medium {!fBillable ? 'border-brand bg-brand text-white' : 'border-neutral-300 text-neutral-600'}">
          {t("time.not_billable")}
        </button>
        <button type="button" onclick={() => (fBillable = true)}
          class="rounded-lg border px-3 py-2 text-sm font-medium {fBillable ? 'border-brand bg-brand text-white' : 'border-neutral-300 text-neutral-600'}">
          {t("time.billable")}
        </button>
      </div>

      <div>
        <label for="date" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.date")}</label>
        <input id="date" name="date" type="date" value={data.selectedDate} required class={inputClass} />
      </div>
      <div>
        <label for="company_id" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.company")}</label>
        <select id="company_id" name="company_id" bind:value={fCompany} class={inputClass}>
          <option value="">{t("common.none")}</option>
          {#each data.companies as company (company.id)}<option value={company.id}>{company.name}</option>{/each}
        </select>
      </div>
      <div>
        <label for="project_id" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.project")}</label>
        <select id="project_id" name="project_id" class={inputClass}>
          <option value="">{t("common.none")}</option>
          {#each formProjects as project (project.id)}<option value={project.id}>{project.name}</option>{/each}
        </select>
      </div>
      <div>
        <label for="task_id" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.task")}</label>
        <select id="task_id" name="task_id" class={inputClass}>
          <option value="">{t("common.none")}</option>
          {#each data.tasks as task (task.id)}<option value={task.id}>{task.title}</option>{/each}
        </select>
      </div>
      <div>
        <label for="description" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.description")}</label>
        <textarea id="description" name="description" rows="2" class={inputClass}></textarea>
      </div>

      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <button class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </form>
  </aside>
</div>
