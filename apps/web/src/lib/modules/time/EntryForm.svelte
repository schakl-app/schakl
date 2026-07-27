<script lang="ts">
  /**
   * The one time-entry form, used for both create and edit. Start/end/break and the free-text
   * duration field stay in sync live: edit the times and the duration updates; type a duration
   * ("1:30", "90m", "1,5") and the end time is back-computed.
   */
  import { enhance } from "$app/forms";
  import { beforeNavigate } from "$app/navigation";
  import { page } from "$app/state";
  import { burnBarClass, burnBarWidth, burnPct, burnTextClass } from "$lib/core/burn";
  import { fmtDateTime, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import {
    endFromDuration,
    formatDurationInput,
    minutesBetween,
    parseDurationText,
  } from "$lib/modules/time/duration";
  import {
    entryTypeLabel,
    entryTypes,
    formatMinutes,
    type TimeEntryTypeDef,
  } from "$lib/modules/time/format";

  interface Option {
    id: string;
    name?: string;
    title?: string;
    company_id?: string | null;
    project_id?: string | null;
    allocated_minutes?: number | null;
    // What a new entry on this project bills by default (#284): false on a project a
    // subscription covers, so retainer work is never charged twice.
    billable_default?: boolean;
    // Budget burn (#112): present when the caller's lookup asked the API for `hours=true`.
    hours?: {
      budget_hours?: number | null;
      spent_hours?: number;
      billable_hours?: number;
      remaining_hours?: number | null;
    } | null;
    // The agreements this project's hours come from (#225) — non-empty means the budget above
    // *is* the retainer's included hours, which is why the form no longer picks a subscription.
    budget_sources?: { subscription_id: string; name: string }[] | null;
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
    entry_type_key?: string | null;
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
    draftDate = null,
    draftInitial = null,
    draftSavedAt = null,
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
    /** The day this form autosaves its draft under (#44); null disables autosave (edit mode,
     *  report modal). Create-only — an existing entry is its own persistence. */
    draftDate?: string | null;
    /** A previously autosaved payload to restore, from the day view's ride-along. */
    draftInitial?: Record<string, unknown> | null;
    /** When the restored draft was last saved (ISO), for the quiet status line. */
    draftSavedAt?: string | null;
    oncancel?: () => void;
    ondone?: () => void;
    /** When provided, typing an unknown client/project name offers to create it inline. */
    oncreatecompany?: (name: string) => void;
    /** The form's currently-picked client rides along (#247), so the project quick-create
     *  dialog opens with the same client instead of blank. */
    oncreateproject?: (name: string, companyId: string) => void;
  } = $props();

  // --- form state (prefilled when editing; a restored draft fills the create form, #44) ---
  const restored = (entry ? null : draftInitial) as {
    date?: string | null;
    start?: string | null;
    end?: string | null;
    break_minutes?: number | null;
    duration_text?: string | null;
    billable?: boolean | null;
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
    description?: string | null;
    entry_type_key?: string | null;
  } | null;
  let fDate = $state(entry ? entry.started_at.slice(0, 10) : (restored?.date ?? date));
  let fStart = $state(entry ? entry.started_at.slice(11, 16) : (restored?.start ?? ""));
  let fEnd = $state(entry?.ended_at ? entry.ended_at.slice(11, 16) : (restored?.end ?? ""));
  let fBreak = $state(entry?.break_minutes ?? restored?.break_minutes ?? 0);
  /** What a new entry on this project bills by default (#284) — false where a subscription
   *  covers it, because the retainer already pays for that work. Mirrors what the API
   *  resolves when a client sends no `billable` at all; no project means the old plain true. */
  function projectBillable(projectId: string): boolean {
    if (!projectId) return true;
    return projects.find((p) => p.id === projectId)?.billable_default ?? true;
  }
  const initialProject = entry?.project_id ?? restored?.project_id ?? defaultProjectId;
  let fBillable = $state(entry?.billable ?? restored?.billable ?? projectBillable(initialProject));
  // An entry being edited, and a restored draft, both carry a billable the person already
  // settled — picking a project must not quietly overwrite it (#284).
  let billableTouched = Boolean(entry) || restored?.billable != null;
  function setBillable(value: boolean) {
    fBillable = value;
    billableTouched = true;
  }
  let fCompany = $state(entry?.company_id ?? restored?.company_id ?? defaultCompanyId);
  let fProject = $state(initialProject);
  let fTask = $state(entry?.task_id ?? restored?.task_id ?? "");
  let fDescription = $state(entry?.description ?? restored?.description ?? "");
  const locale = $derived((page.data.locale as string | undefined) ?? "nl");
  // Deliberate initial capture, like every f* seed above.
  // svelte-ignore state_referenced_locally
  let fType = $state(entry?.entry_type_key ?? restored?.entry_type_key ?? "");
  // Tenant-defined types (#176), fetched once per session (module-level cache); the list
  // shows the active ones plus the entry's own type when that has been deactivated.
  let allTypes = $state<TimeEntryTypeDef[]>([]);
  $effect(() => {
    void entryTypes().then((fetched) => (allTypes = fetched));
  });
  const typeOptions = $derived(allTypes.filter((et) => et.active || et.key === fType));
  let durationText = $state(restored?.duration_text ?? "");
  let confirmDelete = $state(false);

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
    (fCompany ? projects.filter((p) => p.company_id === fCompany || !p.company_id) : projects).map(
      (p) => ({ value: p.id, label: p.name ?? "" }),
    ),
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
    // The project seeds "is this billable" (#284) — and a project a subscription covers seeds
    // *not* billable, because the retainer already pays for the work. Only until the person
    // says otherwise: once they have touched the toggle themselves, switching projects never
    // overrules them. The API resolves the same default when a client sends nothing, so this
    // is the form showing the answer up front, not deciding it.
    if (project && !billableTouched) fBillable = project.billable_default ?? true;
  }

  // Budget feedback where the hours are spent (#112): the person logging sees how much of the
  // picked project's budget is left *before* saving, not on another screen afterwards. Hours
  // only — money is priced per logging employee (#226), so there is no client-side rate to
  // draw a euro figure from here.
  //
  // This is also the *only* place a retainer's included hours surface while logging: an entry
  // no longer links to a subscription, it links to a project, and a covered project's budget
  // **is** the agreement's included hours (#225). One number, named after where it comes from.
  const pickedProject = $derived(fProject ? projects.find((p) => p.id === fProject) : undefined);
  const pickedBurn = $derived.by(() => {
    const hours = pickedProject?.hours;
    if (!hours || hours.budget_hours == null) return null;
    const spent = hours.spent_hours ?? 0;
    return {
      spent,
      budget: hours.budget_hours,
      // The API's own remainder (unclamped, so an over-budget project reads negative).
      remaining: hours.remaining_hours ?? Math.round((hours.budget_hours - spent) * 100) / 100,
      pct: burnPct(spent, hours.budget_hours),
      sources: pickedProject?.budget_sources ?? [],
    };
  });

  // --- draft autosave (#44) ---------------------------------------------------
  // Never silently lose typed input: the create form autosaves ~1s after the last change,
  // flushes before navigation, and beacons on tab close / PWA backgrounding. Pristine never
  // writes a row — "dirty" means *differs from the day's defaults*, not *differs from empty*,
  // or merely visiting a day would create a draft.
  const draftEnabled = Boolean(draftDate) && !entry;
  let hasConcept = $state(Boolean(draftInitial));
  let conceptSavedAt = $state<string | null>(draftSavedAt ?? null);
  let draftTimer: ReturnType<typeof setTimeout> | undefined;
  let sawFirstRun = false;

  function draftPayload(): Record<string, unknown> {
    return {
      date: fDate || null,
      start: fStart || null,
      end: fEnd || null,
      break_minutes: Number(fBreak) || 0,
      duration_text: durationText || null,
      billable: fBillable,
      company_id: fCompany || null,
      project_id: fProject || null,
      task_id: fTask || null,
      description: fDescription || null,
      entry_type_key: fType || null,
    };
  }
  const pristine = JSON.stringify({
    date: date || null,
    start: null,
    end: null,
    break_minutes: 0,
    duration_text: null,
    // The seeded value, not a flat `true` (#284): with a retainer project pre-filled the form
    // opens on "niet factureerbaar", and a baseline of `true` would read that as typed input
    // and autosave a draft for a day nobody touched.
    billable: projectBillable(defaultProjectId),
    company_id: defaultCompanyId || null,
    project_id: defaultProjectId || null,
    task_id: null,
    description: null,
    entry_type_key: null,
  });

  async function saveDraft(payload: Record<string, unknown>): Promise<void> {
    try {
      const res = await fetch("/time/draft", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ date: draftDate, payload }),
      });
      if (res.ok) {
        const body = (await res.json()) as { updated_at?: string | null };
        hasConcept = true;
        conceptSavedAt = body.updated_at ?? new Date().toISOString();
      }
    } catch {
      // Offline / flaky network: the next change reschedules the save.
    }
  }

  async function discardDraft(): Promise<void> {
    clearTimeout(draftTimer);
    hasConcept = false;
    conceptSavedAt = null;
    fDate = date;
    fStart = "";
    fEnd = "";
    fBreak = 0;
    fBillable = true;
    fCompany = defaultCompanyId;
    fProject = defaultProjectId;
    fTask = "";
    fDescription = "";
    durationText = "";
    sawFirstRun = false; // the reset itself must not re-save
    try {
      await fetch("/time/draft", {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ date: draftDate }),
      });
    } catch {
      // A failed discard leaves the row for the retention cron; nothing to surface.
    }
  }

  $effect(() => {
    const payload = draftPayload(); // reads every field — the effect's dependencies
    if (!draftEnabled) return;
    if (!sawFirstRun) {
      sawFirstRun = true; // mounting (or restoring a draft) is not an edit
      return;
    }
    if (!hasConcept && JSON.stringify(payload) === pristine) return;
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => void saveDraft(payload), 1000);
  });

  beforeNavigate(() => {
    if (!draftEnabled || draftTimer === undefined) return;
    clearTimeout(draftTimer);
    draftTimer = undefined;
    const payload = draftPayload();
    if (hasConcept || JSON.stringify(payload) !== pristine) void saveDraft(payload);
  });

  $effect(() => {
    if (!draftEnabled) return;
    const flush = () => {
      const payload = draftPayload();
      if (!hasConcept && JSON.stringify(payload) === pristine) return;
      navigator.sendBeacon(
        "/time/draft",
        new Blob([JSON.stringify({ date: draftDate, payload })], { type: "application/json" }),
      );
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flush();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", flush);
    };
  });

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // Save in flight (#242): spinner on the button, no double submit.
  const busy = new InFlight();
</script>

<form
  method="POST"
  {action}
  use:enhance={busy.wrap("", () => async ({ result, update }) => {
    if (result.type === "success" && draftEnabled) {
      // The entry landed and the API cleared the draft with it (#44).
      clearTimeout(draftTimer);
      draftTimer = undefined;
      hasConcept = false;
      conceptSavedAt = null;
      sawFirstRun = false;
    }
    ondone?.();
    await update({ reset: !entry });
  })}
  class="space-y-3"
>
  {#if entry}<input type="hidden" name="id" value={entry.id} />{/if}

  <!-- Restored/autosaved draft (#44): a quiet line, never a toast; discard is one click. -->
  {#if draftEnabled && hasConcept}
    <div class="flex items-center justify-between text-xs text-text-muted">
      <span class="inline-flex items-center gap-2">
        <span class="rounded-full border border-border bg-surface px-2 py-0.5 font-medium">
          {t("time.draft.chip")}
        </span>
        {#if conceptSavedAt}
          <span>{t("time.draft.saved", { time: fmtDateTime(conceptSavedAt) })}</span>
        {/if}
      </span>
      <button
        type="button"
        class="hover:text-red-600 dark:hover:text-red-400"
        onclick={() => void discardDraft()}
      >
        {t("time.draft.discard")}
      </button>
    </div>
  {/if}

  <div class="grid grid-cols-3 gap-2">
    <div>
      <label for="start-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.start")}</label
      >
      <TimeInput
        id="start-{action}"
        name="start"
        required
        bind:value={fStart}
        onchange={syncDurationFromTimes}
      />
    </div>
    <div>
      <label for="end-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.end")}</label
      >
      <TimeInput
        id="end-{action}"
        name="end"
        required
        bind:value={fEnd}
        onchange={syncDurationFromTimes}
      />
    </div>
    <div>
      <label for="break-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.break")}</label
      >
      <input
        id="break-{action}"
        name="break_minutes"
        type="number"
        min="0"
        step="5"
        bind:value={fBreak}
        oninput={syncDurationFromTimes}
        class={inputClass}
      />
    </div>
  </div>

  <div class="flex items-center gap-3">
    <div class="flex-1">
      <label for="duration-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.duration")}</label
      >
      <input
        id="duration-{action}"
        bind:value={durationText}
        onchange={syncEndFromDuration}
        placeholder={t("time.duration_hint")}
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
      onclick={() => setBillable(false)}
      class="rounded-lg border px-3 py-2 text-sm font-medium {!fBillable
        ? 'border-brand bg-brand text-white'
        : 'border-border text-text-muted'}"
    >
      {t("time.not_billable")}
    </button>
    <button
      type="button"
      onclick={() => setBillable(true)}
      class="rounded-lg border px-3 py-2 text-sm font-medium {fBillable
        ? 'border-brand bg-brand text-white'
        : 'border-border text-text-muted'}"
    >
      {t("time.billable")}
    </button>
  </div>

  <div>
    <label for="date-{action}" class="mb-1 block text-xs font-medium text-text-muted"
      >{t("time.field.date")}</label
    >
    <DateInput id="date-{action}" name="date" bind:value={fDate} required />
  </div>
  <div>
    <label for="company-{action}" class="mb-1 block text-xs font-medium text-text-muted"
      >{t("time.field.company")}</label
    >
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
    <label for="project-{action}" class="mb-1 block text-xs font-medium text-text-muted"
      >{t("time.field.project")}</label
    >
    <Combobox
      items={projectOptions}
      name="project_id"
      bind:value={fProject}
      id="project-{action}"
      placeholder={t("time.field.project")}
      onselect={onProjectPicked}
      oncreate={oncreateproject ? (name) => oncreateproject(name, fCompany) : undefined}
    />
    {#if pickedBurn}
      <div class="mt-2 rounded-lg border border-border bg-surface p-2.5">
        <div class="flex items-baseline justify-between gap-2">
          <span class="text-xs text-text-muted">{t("time.budget.remaining_label")}</span>
          <!-- Loud when it's gone (UX Principle 4): the same green/amber/red scale as every
               other burn surface, on the figure the person logging actually needs. -->
          <span class="text-sm font-semibold tabular-nums {burnTextClass(pickedBurn.pct)}">
            {pickedBurn.remaining < 0
              ? t("time.budget.over", { hours: fmtNumber(-pickedBurn.remaining, 1) })
              : t("time.budget.remaining", { hours: fmtNumber(pickedBurn.remaining, 1) })}
          </span>
        </div>
        {#if pickedBurn.pct != null}
          <div class="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-raised">
            <!-- The one burn scale (core/burn.ts): the number may exceed 100 %, the bar can't. -->
            <div
              class="h-full rounded-full {burnBarClass(pickedBurn.pct)}"
              style="width: {burnBarWidth(pickedBurn.pct)}%"
            ></div>
          </div>
        {/if}
        <div class="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
          <span class="text-xs tabular-nums text-text-muted">
            {t("time.budget.spent", {
              spent: fmtNumber(pickedBurn.spent, 1),
              budget: fmtNumber(pickedBurn.budget, 1),
            })}
          </span>
          {#if pickedBurn.sources.length > 0}
            <!-- Where the budget comes from: these hours are a retainer's included hours (#225),
                 which is what the entry form used to ask for with a subscription picker. -->
            <span class="min-w-0 truncate text-xs text-text-muted">
              {t("time.budget.from_subscription", {
                name: pickedBurn.sources.map((s) => s.name).join(", "),
              })}
            </span>
          {/if}
        </div>
      </div>
    {:else if pickedProject?.hours}
      <!-- Only when the caller asked for the burn and the answer was "no budget". A lookup
           fetched without `hours=true` knows nothing, and silence beats a false "geen budget". -->
      <p class="mt-1.5 text-xs text-text-muted">{t("time.budget.none")}</p>
    {/if}
  </div>
  <div>
    <label for="task-{action}" class="mb-1 block text-xs font-medium text-text-muted"
      >{t("time.field.task")}</label
    >
    <Combobox
      items={taskOptions}
      name="task_id"
      bind:value={fTask}
      id="task-{action}"
      placeholder={t("time.field.task")}
    />
  </div>
  <div>
    <label for="description-{action}" class="mb-1 block text-xs font-medium text-text-muted"
      >{t("time.field.description")}</label
    >
    <textarea
      id="description-{action}"
      name="description"
      rows="2"
      class={inputClass}
      bind:value={fDescription}></textarea>
  </div>
  {#if typeOptions.length > 0}
    <div>
      <label for="entry-type-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.entry_type")}</label
      >
      <!-- A closed vocabulary is still a type-ahead (docs/UX.md): the tenant's types are org
           config, so there is no inline-create path here — no `oncreate`, no ＋. -->
      <Combobox
        items={typeOptions.map((option) => ({
          value: option.key,
          label: entryTypeLabel(option, locale),
        }))}
        name="entry_type_key"
        bind:value={fType}
        id="entry-type-{action}"
        placeholder={t("time.field.entry_type")}
      />
    </div>
  {/if}

  {#if error}<p class="text-sm text-red-600">{t(error)}</p>{/if}
  <div class="flex gap-2">
    <Button type="submit" loading={busy.active} class="flex-1">
      {t("common.save")}
    </Button>
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
  {#if entry && deleteAction}
    <button
      type="button"
      onclick={() => (confirmDelete = true)}
      class="w-full rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:border-red-300 hover:text-red-600 dark:hover:border-red-800 dark:hover:text-red-400"
    >
      {t("common.delete")}
    </button>
  {/if}
</form>

{#if entry && deleteAction}
  <ConfirmDialog
    bind:open={confirmDelete}
    title={t("time.delete")}
    message={t("time.delete_confirm")}
    action={deleteAction}
    fields={{ id: entry.id }}
  />
{/if}
