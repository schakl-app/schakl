<script lang="ts">
  /**
   * The one time-entry form, used for both create and edit. Start/end/break and the free-text
   * duration field stay in sync live: edit the times and the duration updates; type a duration
   * ("1:30", "90m", "1,5") and the end time is back-computed.
   */
  import { enhance } from "$app/forms";
  import { beforeNavigate } from "$app/navigation";
  import { page } from "$app/state";
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
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
    // Budget burn (#112): present when the caller's lookup asked the API for `hours=true`.
    hours?: {
      budget_hours?: number | null;
      spent_hours?: number;
      billable_hours?: number;
      remaining_hours?: number | null;
    } | null;
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
    subscription_id?: string | null;
    entry_type_key?: string | null;
  }

  interface SubscriptionOption {
    id: string;
    name: string;
    company_id?: string | null;
  }

  let {
    action,
    entry = null,
    date,
    companies,
    projects,
    tasks,
    subscriptions = [],
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
    /** Active subscriptions for the optional agreement link; empty hides the picker. */
    subscriptions?: SubscriptionOption[];
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
    oncreateproject?: (name: string) => void;
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
    subscription_id?: string | null;
    description?: string | null;
    entry_type_key?: string | null;
  } | null;
  let fDate = $state(entry ? entry.started_at.slice(0, 10) : (restored?.date ?? date));
  let fStart = $state(entry ? entry.started_at.slice(11, 16) : (restored?.start ?? ""));
  let fEnd = $state(entry?.ended_at ? entry.ended_at.slice(11, 16) : (restored?.end ?? ""));
  let fBreak = $state(entry?.break_minutes ?? restored?.break_minutes ?? 0);
  let fBillable = $state(entry?.billable ?? restored?.billable ?? true);
  let fCompany = $state(entry?.company_id ?? restored?.company_id ?? defaultCompanyId);
  let fProject = $state(entry?.project_id ?? restored?.project_id ?? defaultProjectId);
  let fTask = $state(entry?.task_id ?? restored?.task_id ?? "");
  let fSubscription = $state(entry?.subscription_id ?? restored?.subscription_id ?? "");
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
  }

  // Subscription picker (owner request): narrowed to the picked client; picking one
  // back-fills the client, exactly like the project picker — the API enforces the same pair.
  const subscriptionOptions = $derived(
    (fCompany ? subscriptions.filter((s) => s.company_id === fCompany) : subscriptions).map(
      (s) => ({ value: s.id, label: s.name }),
    ),
  );
  function onSubscriptionPicked(subscriptionId: string) {
    const sub = subscriptions.find((s) => s.id === subscriptionId);
    if (sub?.company_id) fCompany = sub.company_id;
  }

  // Budget feedback where the hours are spent (#112): the person logging sees how much of the
  // picked project's budget is left *before* saving, not on another screen afterwards. Hours
  // only — money is priced per logging employee (#226), so there is no client-side rate to
  // draw a euro figure from here.
  const pickedProject = $derived(fProject ? projects.find((p) => p.id === fProject) : undefined);
  const pickedBurn = $derived.by(() => {
    const hours = pickedProject?.hours;
    if (!hours || hours.budget_hours == null) return null;
    return {
      spent: hours.spent_hours ?? 0,
      budget: hours.budget_hours,
      pct: burnPct(hours.spent_hours ?? 0, hours.budget_hours),
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
      subscription_id: fSubscription || null,
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
    billable: true,
    company_id: defaultCompanyId || null,
    project_id: defaultProjectId || null,
    task_id: null,
    subscription_id: null,
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
    fSubscription = "";
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
      oncreate={oncreateproject}
    />
    {#if pickedBurn}
      <div class="mt-1.5">
        <div class="flex items-center justify-between text-xs text-text-muted">
          <span class="tabular-nums">
            {t("time.budget.spent", {
              spent: fmtNumber(pickedBurn.spent, 1),
              budget: fmtNumber(pickedBurn.budget, 1),
            })}
          </span>
        </div>
        {#if pickedBurn.pct != null}
          <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-surface">
            <!-- The one burn scale (core/burn.ts): the number may exceed 100 %, the bar can't. -->
            <div
              class="h-full rounded-full {burnBarClass(pickedBurn.pct)}"
              style="width: {burnBarWidth(pickedBurn.pct)}%"
            ></div>
          </div>
        {/if}
      </div>
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
  {#if subscriptions.length > 0}
    <div>
      <label for="subscription-{action}" class="mb-1 block text-xs font-medium text-text-muted"
        >{t("time.field.subscription")}</label
      >
      <Combobox
        items={subscriptionOptions}
        name="subscription_id"
        bind:value={fSubscription}
        id="subscription-{action}"
        placeholder={t("time.field.subscription")}
        onselect={onSubscriptionPicked}
      />
    </div>
  {/if}
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
      <select id="entry-type-{action}" name="entry_type_key" bind:value={fType} class={inputClass}>
        <option value="">{t("time.entry_type_none")}</option>
        {#each typeOptions as option (option.key)}
          <option value={option.key}>{entryTypeLabel(option, locale)}</option>
        {/each}
      </select>
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
