<script lang="ts">
  /**
   * The one time-entry form, used for both create and edit. Start/end/break and the free-text
   * duration field stay in sync live: edit the times and the duration updates; type a duration
   * ("1:30", "90m", "1,5") and the end time is back-computed.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import {
    endFromDuration,
    formatDurationInput,
    minutesBetween,
    parseDurationText,
  } from "$lib/modules/time/duration";
  import { formatMinutes } from "$lib/modules/time/format";

  interface Option {
    id: string;
    name?: string;
    title?: string;
    company_id?: string | null;
    project_id?: string | null;
    allocated_minutes?: number | null;
  }

  interface EntryLike {
    id: string;
    started_at: string;
    ended_at?: string | null;
    break_minutes?: number;
    billable?: boolean;
    description?: string | null;
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
  }

  let {
    action,
    entry = null,
    date,
    companies,
    projects,
    tasks,
    defaultCompanyId = "",
    defaultProjectId = "",
    error = null,
    deleteAction = null,
    oncancel,
    ondone,
    oncreatecompany,
    oncreateproject,
  }: {
    action: string;
    entry?: EntryLike | null;
    date: string;
    companies: Option[];
    projects: Option[];
    tasks: Option[];
    defaultCompanyId?: string;
    defaultProjectId?: string;
    error?: string | null;
    /** When set (edit mode), renders a delete button submitting to this action. */
    deleteAction?: string | null;
    oncancel?: () => void;
    ondone?: () => void;
    /** When provided, typing an unknown client/project name offers to create it inline. */
    oncreatecompany?: (name: string) => void;
    oncreateproject?: (name: string) => void;
  } = $props();

  // --- form state (prefilled when editing) -----------------------------------
  let fDate = $state(entry ? entry.started_at.slice(0, 10) : date);
  let fStart = $state(entry ? entry.started_at.slice(11, 16) : "");
  let fEnd = $state(entry?.ended_at ? entry.ended_at.slice(11, 16) : "");
  let fBreak = $state(entry?.break_minutes ?? 0);
  let fBillable = $state(entry?.billable ?? true);
  let fCompany = $state(entry?.company_id ?? defaultCompanyId);
  let fProject = $state(entry?.project_id ?? defaultProjectId);
  let fTask = $state(entry?.task_id ?? "");
  let durationText = $state("");

  // Live worked minutes from the times (span minus break).
  const workedMinutes = $derived.by(() => {
    if (!fStart || !fEnd) return null;
    const span = minutesBetween(fStart, fEnd);
    if (span == null) return null;
    return Math.max(0, span - (Number(fBreak) || 0));
  });

  // Editing the times rewrites the duration text; editing the duration rewrites the end.
  function syncDurationFromTimes() {
    durationText = workedMinutes != null ? formatDurationInput(workedMinutes) : "";
  }
  function syncEndFromDuration() {
    const minutes = parseDurationText(durationText);
    if (minutes == null) return;
    const start = fStart || "09:00";
    fStart = start;
    const end = endFromDuration(start, minutes, Number(fBreak) || 0);
    if (end) fEnd = end;
  }

  $effect(() => {
    // Prefill the duration text when editing an existing entry.
    if (entry && !durationText && workedMinutes != null) {
      durationText = formatDurationInput(workedMinutes);
    }
  });

  const projectOptions = $derived(
    (fCompany
      ? projects.filter((p) => p.company_id === fCompany || !p.company_id)
      : projects
    ).map((p) => ({ value: p.id, label: p.name ?? "" })),
  );
  const taskOptions = $derived(
    (fProject
      ? tasks.filter((task) => task.project_id === fProject || !task.project_id)
      : tasks
    ).map((task) => ({
      value: task.id,
      label: task.title ?? "",
      hint: task.allocated_minutes ? formatMinutes(task.allocated_minutes) : undefined,
    })),
  );
  function onProjectPicked(projectId: string) {
    const project = projects.find((p) => p.id === projectId);
    if (project?.company_id) fCompany = project.company_id;
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<form
  method="POST"
  {action}
  use:enhance={() => ({ update }) => {
    ondone?.();
    void update({ reset: !entry });
  }}
  class="space-y-3"
>
  {#if entry}<input type="hidden" name="id" value={entry.id} />{/if}

  <div class="grid grid-cols-3 gap-2">
    <div>
      <label for="start-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.start")}</label>
      <input id="start-{action}" name="start" type="time" required bind:value={fStart}
        oninput={syncDurationFromTimes} class={inputClass} />
    </div>
    <div>
      <label for="end-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.end")}</label>
      <input id="end-{action}" name="end" type="time" required bind:value={fEnd}
        oninput={syncDurationFromTimes} class={inputClass} />
    </div>
    <div>
      <label for="break-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.break")}</label>
      <input id="break-{action}" name="break_minutes" type="number" min="0" step="5" bind:value={fBreak}
        oninput={syncDurationFromTimes} class={inputClass} />
    </div>
  </div>

  <div class="flex items-center gap-3">
    <div class="flex-1">
      <label for="duration-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.duration")}</label>
      <input id="duration-{action}" bind:value={durationText} onchange={syncEndFromDuration}
        placeholder={t("time.duration_hint")} class={inputClass} />
    </div>
    <div class="pt-5 text-sm font-semibold tabular-nums {workedMinutes ? 'text-brand' : 'text-neutral-300'}">
      {workedMinutes != null ? t("time.worked", { duration: formatMinutes(workedMinutes) }) : "—"}
    </div>
  </div>

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
    <label for="date-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.date")}</label>
    <DateInput id="date-{action}" name="date" bind:value={fDate} required />
  </div>
  <div>
    <label for="company-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.company")}</label>
    <Combobox
      items={companies.map((c) => ({ value: c.id, label: c.name ?? "" }))}
      name="company_id"
      bind:value={fCompany}
      id="company-{action}"
      placeholder={t("time.field.company")}
      oncreate={oncreatecompany}
    />
  </div>
  <div>
    <label for="project-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.project")}</label>
    <Combobox items={projectOptions} name="project_id" bind:value={fProject}
      id="project-{action}" placeholder={t("time.field.project")} onselect={onProjectPicked}
      oncreate={oncreateproject} />
  </div>
  <div>
    <label for="task-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.task")}</label>
    <Combobox items={taskOptions} name="task_id" bind:value={fTask}
      id="task-{action}" placeholder={t("time.field.task")} />
  </div>
  <div>
    <label for="description-{action}" class="mb-1 block text-xs font-medium text-neutral-500">{t("time.field.description")}</label>
    <textarea id="description-{action}" name="description" rows="2" class={inputClass}>{entry?.description ?? ""}</textarea>
  </div>

  {#if error}<p class="text-sm text-red-600">{t(error)}</p>{/if}
  <div class="flex gap-2">
    <button class="flex-1 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
      {t("common.save")}
    </button>
    {#if oncancel}
      <button type="button" class="rounded-lg border border-neutral-300 px-4 py-2 text-sm" onclick={oncancel}>
        {t("common.cancel")}
      </button>
    {/if}
  </div>
  {#if entry && deleteAction}
    <button
      formaction={deleteAction}
      formnovalidate
      class="w-full rounded-lg border border-neutral-300 px-4 py-2 text-sm text-neutral-500 hover:border-red-300 hover:text-red-600"
    >
      {t("common.delete")}
    </button>
  {/if}
</form>
