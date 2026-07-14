<script lang="ts">
  /**
   * Schedule a task onto the calendar (#188), shared by the calendar "+" and the task detail
   * page so both entry points behave identically.
   *
   * Two ways in:
   * - **Preselected** (`task` set, from the task page): jump straight to the form.
   * - **Picker** (`pickerEndpoint` set, from the calendar): a dataframe of schedulable tasks —
   *   task, project, client, assignee, time budget — fetched lazily on open; pick one, then fill
   *   the form.
   *
   * The form works in the org's *local* calendar (day + start time + length); the API owns the
   * timezone and returns instants (§8). The length defaults to the task's time budget but is
   * editable, and a live preview shows exactly the block that will be stored, and its end.
   */
  import { enhance } from "$app/forms";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import { durationMinutes as blockDuration, localDayTime } from "./schedule";

  interface SchedTask {
    id: string;
    title: string;
    project_id?: string | null;
    company_id?: string | null;
    assignee_user_id?: string | null;
    allocated_minutes?: number | null;
    due_date?: string | null;
  }
  interface Member {
    user_id: string;
    full_name: string | null;
    email: string;
  }
  interface NameRef {
    id: string;
    name: string;
  }

  interface EditBlock {
    id: string;
    starts_at: string;
    ends_at: string;
    user_id: string | null;
    note: string | null;
  }

  let {
    open = $bindable(false),
    task = null,
    editBlock = null,
    members = [],
    companies = [],
    projects = [],
    currentUserId,
    canScheduleAny = false,
    defaultDate = null,
    pickerEndpoint = null,
    action = "?/scheduleTask",
    ondone,
  }: {
    open?: boolean;
    /** Preselected task (task detail page); null → picker mode. */
    task?: SchedTask | null;
    /** Editing an existing block (its task fixed); prefills day/time/length from the block. */
    editBlock?: EditBlock | null;
    members?: Member[];
    companies?: NameRef[];
    projects?: NameRef[];
    currentUserId: string;
    /** Whether the person may be anyone (`:any`) or is fixed to the current user (`:own`). */
    canScheduleAny?: boolean;
    /** Prefill the date with the calendar's current day. */
    defaultDate?: string | null;
    /** When set (calendar), fetch schedulable tasks + lookups from here on open. */
    pickerEndpoint?: string | null;
    action?: string;
    ondone?: () => void;
  } = $props();

  function todayIso(): string {
    return new Date().toISOString().slice(0, 10);
  }

  let loading = $state(false);
  let loaded = $state(false);
  let pickerTasks = $state<SchedTask[]>([]);
  let pickerMembers = $state<Member[]>([]);
  let pickerCompanies = $state<NameRef[]>([]);
  let pickerProjects = $state<NameRef[]>([]);
  let search = $state("");

  let selectedTask = $state<SchedTask | null>(null);
  let personId = $state("");
  let day = $state("");
  let startTime = $state("09:00");
  let durationMinutes = $state(60);
  let note = $state("");
  let submitting = $state(false);
  let errorKey = $state<string | null>(null);

  const allMembers = $derived(pickerMembers.length ? pickerMembers : members);
  const companyName = $derived(
    new Map((pickerCompanies.length ? pickerCompanies : companies).map((c) => [c.id, c.name])),
  );
  const projectName = $derived(
    new Map((pickerProjects.length ? pickerProjects : projects).map((p) => [p.id, p.name])),
  );
  const memberName = $derived(
    new Map(allMembers.map((m) => [m.user_id, m.full_name || m.email])),
  );
  const personOptions = $derived(
    allMembers.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );

  const filteredTasks = $derived(
    search.trim()
      ? pickerTasks.filter((task) =>
          task.title.toLowerCase().includes(search.trim().toLowerCase()),
        )
      : pickerTasks,
  );

  function prefill(picked: SchedTask) {
    selectedTask = picked;
    personId =
      canScheduleAny && picked.assignee_user_id ? picked.assignee_user_id : currentUserId;
    durationMinutes = picked.allocated_minutes && picked.allocated_minutes > 0
      ? picked.allocated_minutes
      : 60;
    day = defaultDate ?? todayIso();
    startTime = "09:00";
    note = "";
    errorKey = null;
  }

  async function loadPicker() {
    if (!pickerEndpoint || loaded) return;
    loading = true;
    try {
      const res = await fetch(pickerEndpoint);
      if (res.ok) {
        const data = await res.json();
        pickerTasks = data.tasks ?? [];
        pickerMembers = data.members ?? [];
        pickerCompanies = data.companies ?? [];
        pickerProjects = data.projects ?? [];
        loaded = true;
      }
    } finally {
      loading = false;
    }
  }

  function prefillEdit(picked: SchedTask, block: EditBlock) {
    selectedTask = picked;
    const { day: d, time } = localDayTime(block.starts_at);
    personId = block.user_id ?? currentUserId;
    day = d;
    startTime = time;
    durationMinutes = blockDuration(block.starts_at, block.ends_at);
    note = block.note ?? "";
    errorKey = null;
  }

  // Reset on each open; edit prefills from the block, a preselected task jumps to the form, and
  // picker mode fetches the schedulable list once.
  $effect(() => {
    if (!open) return;
    errorKey = null;
    if (editBlock && task) {
      prefillEdit(task, editBlock);
    } else if (task) {
      prefill(task);
    } else {
      selectedTask = null;
      search = "";
      void loadPicker();
    }
  });

  // Live preview: the exact block that will be stored, and where it ends.
  const endPreview = $derived.by(() => {
    const [h, m] = startTime.split(":").map(Number);
    if (Number.isNaN(h) || Number.isNaN(m)) return { time: "", nextDay: false };
    const total = h * 60 + m + durationMinutes;
    const time = `${String(Math.floor((total % 1440) / 60)).padStart(2, "0")}:${String(
      total % 60,
    ).padStart(2, "0")}`;
    return { time, nextDay: total >= 1440 };
  });

  const overBudget = $derived(
    Boolean(
      selectedTask?.allocated_minutes &&
        durationMinutes > (selectedTask.allocated_minutes as number),
    ),
  );

  const modalTitle = $derived(
    editBlock
      ? t("tasks.schedule.edit_title")
      : selectedTask
        ? t("tasks.schedule.title")
        : t("tasks.schedule.pick_title"),
  );
</script>

<Modal bind:open size={selectedTask ? "lg" : "2xl"} title={modalTitle}>
  {#if !selectedTask}
    <!-- Picker: a dataframe of schedulable tasks with project, client and assignee visible. -->
    <input
      type="search"
      bind:value={search}
      placeholder={t("tasks.schedule.search")}
      class="mb-3 w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
    />
    {#if loading}
      <p class="py-6 text-center text-sm text-text-muted">{t("common.loading")}</p>
    {:else if filteredTasks.length === 0}
      <p class="py-6 text-center text-sm text-text-muted">{t("tasks.schedule.none")}</p>
    {:else}
      <div class="max-h-[60vh] overflow-auto rounded-lg border border-border">
        <table class="w-full text-left text-sm">
          <thead class="sticky top-0 bg-surface text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th class="px-3 py-2 font-medium">{t("tasks.field.title")}</th>
              <th class="hidden px-3 py-2 font-medium sm:table-cell">{t("tasks.field.project")}</th>
              <th class="hidden px-3 py-2 font-medium sm:table-cell">{t("tasks.field.company")}</th>
              <th class="hidden px-3 py-2 font-medium md:table-cell">{t("tasks.field.assignee")}</th>
              <th class="px-3 py-2 text-right font-medium">{t("tasks.field.allocated")}</th>
            </tr>
          </thead>
          <tbody>
            {#each filteredTasks as row (row.id)}
              <tr
                class="cursor-pointer border-t border-border hover:bg-surface"
                onclick={() => prefill(row)}
              >
                <td class="px-3 py-2 text-text">
                  {row.title}
                  <span class="block text-xs text-text-muted sm:hidden">
                    {[
                      row.project_id ? projectName.get(row.project_id) : null,
                      row.company_id ? companyName.get(row.company_id) : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </td>
                <td class="hidden px-3 py-2 text-text-muted sm:table-cell">
                  {row.project_id ? (projectName.get(row.project_id) ?? "—") : "—"}
                </td>
                <td class="hidden px-3 py-2 text-text-muted sm:table-cell">
                  {row.company_id ? (companyName.get(row.company_id) ?? "—") : "—"}
                </td>
                <td class="hidden px-3 py-2 text-text-muted md:table-cell">
                  {row.assignee_user_id ? (memberName.get(row.assignee_user_id) ?? "—") : "—"}
                </td>
                <td class="px-3 py-2 text-right text-text-muted">
                  {row.allocated_minutes ? formatMinutes(row.allocated_minutes) : "—"}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {:else}
    <!-- Form: person, day, start, length (defaults to the task's time budget), live preview. -->
    <form
      method="POST"
      {action}
      use:enhance={() => {
        submitting = true;
        return async ({ result, update }) => {
          submitting = false;
          if (result.type === "success") {
            open = false;
            ondone?.();
            await update();
          } else if (result.type === "failure") {
            errorKey =
              (result.data as { error?: string } | undefined)?.error ?? "errors.validation";
            await update({ reset: false });
          } else {
            await update();
          }
        };
      }}
      class="space-y-4"
    >
      <input type="hidden" name="task_id" value={selectedTask.id} />
      {#if editBlock}
        <input type="hidden" name="schedule_id" value={editBlock.id} />
      {/if}

      <div class="rounded-lg border border-border bg-surface px-3 py-2">
        <p class="text-sm font-medium text-text">{selectedTask.title}</p>
        <p class="mt-0.5 flex flex-wrap gap-x-3 text-xs text-text-muted">
          {#if selectedTask.allocated_minutes}
            <span>{t("tasks.schedule.budget", { time: formatMinutes(selectedTask.allocated_minutes) })}</span>
          {/if}
          {#if selectedTask.due_date}
            <span>{t("tasks.schedule.deadline_at", { date: fmtDayMonth(selectedTask.due_date) })}</span>
          {/if}
        </p>
        {#if !task}
          <button
            type="button"
            class="mt-1 text-xs text-brand hover:underline"
            onclick={() => (selectedTask = null)}>{t("tasks.schedule.change_task")}</button
          >
        {/if}
      </div>

      <div>
        <span class="mb-1 block text-sm font-medium text-text">{t("tasks.schedule.person")}</span>
        {#if canScheduleAny}
          <Combobox name="user_id" bind:value={personId} items={personOptions} />
        {:else}
          <input type="hidden" name="user_id" value={currentUserId} />
          <p class="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted">
            {memberName.get(currentUserId) ?? t("tasks.schedule.you")}
          </p>
        {/if}
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <span class="mb-1 block text-sm font-medium text-text">{t("tasks.schedule.date")}</span>
          <DateInput name="day" bind:value={day} />
        </div>
        <div>
          <span class="mb-1 block text-sm font-medium text-text">{t("tasks.schedule.start")}</span>
          <TimeInput name="start_time" bind:value={startTime} />
        </div>
        <div>
          <label for="sched-duration" class="mb-1 block text-sm font-medium text-text">
            {t("tasks.schedule.duration")}
          </label>
          <input
            id="sched-duration"
            name="duration_minutes"
            type="number"
            min="15"
            max="1440"
            step="15"
            bind:value={durationMinutes}
            class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
          />
        </div>
      </div>

      <div>
        <label for="sched-note" class="mb-1 block text-sm font-medium text-text">
          {t("tasks.schedule.note")}
        </label>
        <input
          id="sched-note"
          name="note"
          bind:value={note}
          maxlength="500"
          class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
        />
      </div>

      <!-- Live preview: the block that will be stored (docs/UX.md: every number opens). -->
      <p class="rounded-lg bg-surface px-3 py-2 text-sm text-text">
        {fmtDayMonth(day || todayIso())} · {startTime}–{endPreview.time}{endPreview.nextDay
          ? " (+1)"
          : ""} · {formatMinutes(durationMinutes)}
      </p>
      {#if overBudget}
        <p class="text-xs text-amber-600 dark:text-amber-400">{t("tasks.schedule.over_budget")}</p>
      {/if}

      {#if errorKey}
        <p class="text-sm text-red-600 dark:text-red-400">{t(errorKey)}</p>
      {/if}

      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <button
          type="submit"
          disabled={submitting}
          class="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {t("tasks.schedule.submit")}
        </button>
      </div>
    </form>
  {/if}
</Modal>
