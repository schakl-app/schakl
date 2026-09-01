<script lang="ts">
  /**
   * The task you just made, held open beside the record it came from.
   *
   * Approving an e-mail that turned out to *be* new work leaves a task holding a title, a
   * client and — when "laat schakl deze taak invullen" is ticked — a worker about to write its
   * notes, its checklist and its links. The approve used to redirect into that task's edit
   * mode, which finished on the task and lost the inbox: the reviewer read what schakl had
   * written on a page that no longer showed the e-mail it was written from, and came back to
   * a queue that had reloaded under them. This is the dialog form of the same act (docs/UX.md
   * Principle 8 — a dialog is the default, a navigation a deliberate choice): docked right,
   * the message still readable beside it, the task's defining fields editable at once, and a
   * link to the full card for everything a card has that a review does not need.
   *
   * It is a form over a **fetched** row, not a page load, and that decides three things.
   *
   * - **Nobody waits, and nobody is overwritten.** `TaskAIStatus` polls exactly as it does on
   *   the task page; when the run lands, this dialog re-reads the row and adopts the server's
   *   value for every field the reader has *not* touched — so a dialog opened and left alone
   *   simply fills itself in the moment schakl is done, which is the whole request. A field
   *   they were typing in stays theirs, and the strip offers the button instead; pressing it
   *   merges the notes under their words rather than replacing them (the run only ever
   *   *appends* a description, `tasks/system.apply_ai_enrichment_system`).
   * - **The description is a mounted editor**, so a new value is a remount (`{#key}`), never a
   *   prop change it would not see. Its live text is read through `onchange`, which is what
   *   lets "did they touch it" be answered at all.
   * - **The save is the task's own PATCH** through the host page's action, with the fields the
   *   form carries and nothing else — partial like every update action here — and the same
   *   rule the task page enforces: a deadline pushed *later* asks for a reason, inline, because
   *   a second modal over a slide-over is one dialog too many.
   *
   * Every way out ends the same way: `onclose` fires whether the reader saved, closed, pressed
   * Escape, clicked the backdrop or followed the link to the full task, so the host can close
   * the review it was standing on (an exit that discards is Sluiten, never Annuleren — there is
   * nothing to cancel, the task exists).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import SlideOver from "$lib/core/ui/SlideOver.svelte";
  import { projectArchivedLabel } from "$lib/modules/projects/picker";

  import TaskAIStatus from "./TaskAIStatus.svelte";
  import TaskAssigneePicker from "./TaskAssigneePicker.svelte";

  interface Option {
    value: string;
    label: string;
  }
  interface Assignee {
    user_id: string;
    is_primary: boolean;
  }
  interface TaskRow {
    id: string;
    title: string;
    description: string | null;
    company_id: string | null;
    project_id: string | null;
    due_date: string | null;
    assignees: Assignee[];
    assignee_contact_id: string | null;
    ai_status: string | null;
  }

  let {
    open = $bindable(false),
    taskId,
    origin = null,
    projects = [],
    archivedProjects = [],
    members = [],
    action = "?/updateReviewTask",
    onclose,
  }: {
    open?: boolean;
    taskId: string;
    /** Where the task came from — drawn above the form so the context stays on screen. */
    origin?: { label: string; title: string; detail?: string | null } | null;
    /** The project options the host already loaded, split the way its own picker splits them. */
    projects?: Option[];
    archivedProjects?: Option[];
    members?: {
      user_id: string;
      full_name: string | null;
      email: string | null;
      is_active?: boolean;
    }[];
    /** The host page's action; a review posts through the page it is drawn on (docs/UX.md). */
    action?: string;
    onclose?: () => void;
  } = $props();

  const busy = new InFlight();
  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  let task = $state<TaskRow | null>(null);
  let loading = $state(false);
  let error = $state("");

  // The form's live values, and the baseline they started from — the pair is what answers
  // "did the reader touch this" when the run lands.
  let title = $state("");
  let description = $state("");
  let projectId = $state("");
  let dueDate = $state("");
  let dueReason = $state("");
  let baseline = $state({ title: "", description: "", project_id: "", due_date: "" });
  /** Bumped to remount the description editor onto a new value (see the note above). */
  let descriptionKey = $state(0);

  // The client's contacts (#453), fetched once the row says which client — the same endpoint
  // and the same shape `TaskQuickCreate` reads, so the picker here offers what it offers there.
  let contacts = $state<{ id: string; name: string }[]>([]);
  let contactsFor = $state("");

  const dueExtended = $derived(
    Boolean(baseline.due_date) && Boolean(dueDate) && dueDate > baseline.due_date,
  );

  async function fetchTask(): Promise<TaskRow | null> {
    const response = await fetch(`/api/v1/tasks/${taskId}`, {
      headers: { accept: "application/json" },
    });
    if (!response.ok) return null;
    return (await response.json()) as TaskRow;
  }

  function adopt(row: TaskRow) {
    task = row;
    title = row.title;
    description = row.description ?? "";
    projectId = row.project_id ?? "";
    dueDate = row.due_date ?? "";
    baseline = {
      title: row.title,
      description: row.description ?? "",
      project_id: row.project_id ?? "",
      due_date: row.due_date ?? "",
    };
    descriptionKey += 1;
  }

  async function load() {
    loading = true;
    error = "";
    try {
      const row = await fetchTask();
      if (!row) {
        error = "errors.server";
        return;
      }
      adopt(row);
    } catch {
      error = "errors.server";
    } finally {
      loading = false;
    }
  }

  /**
   * The run landed. Take the server's answer for every field the reader left alone; keep
   * theirs where they did not, and say whether that left anything unshown. `forced` is the
   * button — the reader asked — and the one field that can be half-typed is merged, not
   * replaced.
   */
  async function reveal(forced: boolean): Promise<boolean> {
    let row: TaskRow | null;
    try {
      row = await fetchTask();
    } catch {
      row = null;
    }
    if (!row) return false;
    task = row;
    const serverDescription = row.description ?? "";
    let shown = true;
    if (description === baseline.description) {
      description = serverDescription;
      descriptionKey += 1;
    } else if (serverDescription && serverDescription !== baseline.description) {
      if (forced) {
        // What the run added is the part past what the form started from: it appends under a
        // rule, and the reader's own words go first.
        const added = serverDescription.startsWith(baseline.description)
          ? serverDescription.slice(baseline.description.length).replace(/^\s*---\s*/, "")
          : serverDescription;
        description = added.trim() ? `${description.trim()}\n\n${added.trim()}` : description;
        descriptionKey += 1;
      } else {
        shown = false;
      }
    }
    if (title === baseline.title) title = row.title;
    if (projectId === baseline.project_id) projectId = row.project_id ?? "";
    if (dueDate === baseline.due_date) dueDate = row.due_date ?? "";
    baseline = {
      title: row.title,
      description: serverDescription,
      project_id: row.project_id ?? "",
      due_date: row.due_date ?? "",
    };
    return shown;
  }

  $effect(() => {
    if (!open) return;
    void load();
  });

  $effect(() => {
    const companyId = task?.company_id ?? "";
    if (!open || !companyId || companyId === contactsFor) return;
    void (async () => {
      const response = await fetch(`/api/v1/contacts?limit=200&company_id=${companyId}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      interface ContactRow {
        id: string;
        first_name: string;
        last_name?: string | null;
      }
      const rows: ContactRow[] = (await response.json()).items ?? [];
      contacts = rows.map((c) => ({
        id: c.id,
        name: [c.first_name, c.last_name].filter(Boolean).join(" "),
      }));
      contactsFor = companyId;
    })();
  });

  // Every exit — save, Sluiten, Escape, the backdrop, the ✕, the link out — lands here once.
  let shown = $state(false);
  $effect(() => {
    if (open) {
      shown = true;
      return;
    }
    if (shown) {
      shown = false;
      onclose?.();
    }
  });
</script>

<SlideOver bind:open title={t("tasks.review.title")} size="2xl">
  <div class="space-y-4 p-4">
    {#if origin}
      <div class="rounded-xl border border-border bg-surface px-4 py-3 text-sm">
        <p class="text-xs font-medium uppercase tracking-wide text-text-muted">{origin.label}</p>
        <p class="mt-0.5 font-medium text-text">{origin.title}</p>
        {#if origin.detail}
          <p class="mt-0.5 text-xs text-text-muted">{origin.detail}</p>
        {/if}
      </div>
    {/if}

    {#if task?.ai_status}
      <TaskAIStatus taskId={task.id} status={task.ai_status} editing {reveal} />
    {/if}

    {#if loading && !task}
      <p class="text-sm text-text-muted">{t("common.loading")}</p>
    {:else if task}
      <p class="text-sm text-text-muted">{t("tasks.review.hint")}</p>
      <form
        method="POST"
        {action}
        class="space-y-4"
        use:enhance={busy.wrap("save", () => async ({ result, update }) => {
          if (result.type === "failure") {
            error = String(result.data?.error ?? "errors.validation");
            return;
          }
          error = "";
          // Edits what exists: never reset (forms:check).
          await update({ reset: false });
          open = false;
        })}
      >
        <input type="hidden" name="task_id" value={task.id} />
        <div>
          <label for="review-task-title" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.field.title")}</label
          >
          <input
            id="review-task-title"
            name="title"
            bind:value={title}
            required
            class={inputClass}
          />
        </div>
        <div>
          <label for="review-task-description" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.field.description")}</label
          >
          {#key descriptionKey}
            <RichTextEditor
              id="review-task-description"
              name="description"
              rows={6}
              value={description}
              placeholder={t("tasks.detail.description_placeholder")}
              scope={{ companyId: task.company_id ?? null, projectId: projectId || null }}
              onchange={(next) => (description = next)}
            />
          {/key}
        </div>
        <div class="grid gap-4 sm:grid-cols-2">
          <label class="block text-sm">
            <span class="mb-1 block font-medium text-text">{t("tasks.field.project")}</span>
            <Combobox
              items={projects}
              archived={archivedProjects}
              archivedLabel={projectArchivedLabel()}
              name="project_id"
              value={projectId}
              placeholder={t("common.none")}
              onselect={(v) => (projectId = v)}
              id="review-task-project"
            />
          </label>
          <div>
            <label for="review-task-due" class="mb-1 block text-sm font-medium text-text"
              >{t("tasks.field.due_date")}</label
            >
            <DateInput
              name="due_date"
              id="review-task-due"
              value={dueDate}
              required
              onchange={(v) => (dueDate = v)}
            />
          </div>
        </div>
        {#if dueExtended}
          <!-- Accountability (docs/UX.md Principle 4): a later deadline is logged with its
               reason, and the API refuses one without. Inline — a modal over a slide-over is
               one dialog too many. -->
          <div>
            <label for="review-task-due-reason" class="mb-1 block text-sm font-medium text-text"
              >{t("tasks.review.due_reason_label")}</label
            >
            <textarea
              id="review-task-due-reason"
              name="due_change_reason"
              rows="2"
              bind:value={dueReason}
              required
              placeholder={t("tasks.detail.due_reason_placeholder")}
              class={inputClass}></textarea>
            <p class="mt-1 text-xs text-text-muted">{t("tasks.detail.due_reason_hint")}</p>
          </div>
        {/if}
        {#if members.length > 0 || contacts.length > 0}
          <div>
            <span class="mb-1 block text-sm font-medium text-text"
              >{t("tasks.field.assignees")}</span
            >
            <TaskAssigneePicker
              employees={members}
              {contacts}
              contactsEnabled={!!task.company_id && contacts.length > 0}
              assignees={task.assignees}
              contactValue={task.assignee_contact_id ?? ""}
              id="review-task-assignee"
            />
          </div>
        {/if}
        {#if error}
          <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
        {/if}
        <div class="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
          <a href="/tasks/{task.id}" class="text-sm font-medium text-brand hover:underline">
            {t("tasks.review.open_card")}
          </a>
          <div class="flex gap-2">
            <button
              type="button"
              class="rounded-lg border border-border px-4 py-2 text-sm"
              onclick={() => (open = false)}>{t("common.close")}</button
            >
            <Button loading={busy.active}>{t("common.save")}</Button>
          </div>
        </div>
      </form>
    {:else if error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}
  </div>
</SlideOver>
