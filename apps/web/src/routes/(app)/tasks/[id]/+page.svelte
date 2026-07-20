<script lang="ts">
  import { Link as LinkIcon, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { editIntent } from "$lib/core/edit-intent";
  import { fmtDateTime, fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";
  import TaskSchedulePanel from "$lib/modules/tasks/TaskSchedulePanel.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import { entityPanelsFor } from "$lib/core/registry";

  let { data, form } = $props();

  const task = $derived(data.task);

  // Panels contributed by enabled modules (CLAUDE.md §6) — contactmomenten, Drive, and
  // whatever ships later, composed exactly like the project page does.
  const enabledModules = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpecs = $derived(entityPanelsFor(enabledModules, "task"));
  const panelComponent = (key: string) => panelSpecs.find((spec) => spec.key === key)?.component;
  const panelLookups = $derived({
    members: data.members,
    companies: data.companies,
    projects: data.projects,
    // The current task, so a panel can walk task → project → client (e.g. the Drive panel
    // roots the browser at the project/client folder rather than the shared-drive root, #150).
    tasks: task.project_id ? [{ id: task.id, title: task.title, project_id: task.project_id }] : [],
  });

  // The activity log grows without bound on a busy task (issue #86): show the most recent few and
  // expand the rest in place. Rows are newest-first, so the head is the newest.
  const ACTIVITY_COLLAPSED = 3;
  let activityExpanded = $state(false);
  // The task's own legacy trail plus the contact-moment milestones mirrored onto its core
  // activity log (#152) — merged newest-first, so "contactmoment gelogd" shows on the task page
  // like it already does on company/project/contact. Both rows share the same shape
  // (action/payload/actor_name/actor_deleted/created_at), so one feed renders both.
  const activities = $derived(
    [...(task.activities ?? []), ...(data.hostActivity ?? [])]
      // Core rows type `payload` as optional; the renderers want it present. Normalise once.
      .map((a) => ({ ...a, payload: (a.payload ?? {}) as Record<string, unknown> }))
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))),
  );
  const visibleActivities = $derived(
    activityExpanded || activities.length <= ACTIVITY_COLLAPSED
      ? activities
      : activities.slice(0, ACTIVITY_COLLAPSED),
  );
  const userId = $derived(page.data.user?.id ?? "");
  // A portal login (#193) works the task, not the office around it: uploads, the activity
  // trail, time budgets and module panels (interactions, Drive) stay staff-only. The API
  // enforces the same (portal activity feed is empty; time/interactions are permission-gated);
  // this keeps the page honest about it.
  const isPortal = $derived(page.data.user?.isPortal ?? false);
  // `tasks.comment.write:any` lets a manager clean up anyone's comment; the author always can.
  const canDeleteAnyComment = $derived(can(page.data.user, "tasks.comment.write", "any"));

  // The org's configured status vocabulary (issue #62), from the /tasks layout load.
  const statuses = $derived(data.statuses);
  const statusName = (key: string) => statuses.find((s) => s.key === key)?.name ?? key;
  const isDone = $derived(statuses.find((s) => s.key === task.status)?.is_terminal ?? false);

  // Ticking the *last* open to-do offers to finish the task (the to-dos and the status should
  // not drift apart silently). If finishing is gated on a closing contact moment (#157 — the
  // task's own flag, or the terminal status's), the prompt says so instead of offering a move
  // that the API would refuse.
  let showFinishPrompt = $state(false);
  const openItemCount = $derived(
    (task.checklists ?? []).reduce(
      (n, checklist) => n + (checklist.items ?? []).filter((item) => !item.done).length,
      0,
    ),
  );
  const finishStatus = $derived(statuses.find((s) => s.is_terminal) ?? null);
  const finishNeedsMoment = $derived(
    (task.requires_interaction || (finishStatus?.requires_interaction ?? false)) &&
      !task.closing_interaction_id,
  );
  // @mention candidates for the comment composer (issue #63): the org members already loaded,
  // plus — since #165 — the task's company's contacts, fetched lazily in the browser so the
  // SSR load pays nothing for them (docs/PERFORMANCE.md).
  let contactCandidates = $state<
    { id: string; name: string; kind: "contact"; subtitle?: string }[]
  >([]);
  $effect(() => {
    const companyId = task.company_id;
    if (!companyId) {
      contactCandidates = [];
      return;
    }
    void (async () => {
      const response = await fetch(`/api/v1/contacts?limit=200&company_id=${companyId}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      interface ContactRow {
        id: string;
        first_name: string;
        last_name?: string | null;
        companies?: { name: string }[];
      }
      const items: ContactRow[] = (await response.json()).items ?? [];
      contactCandidates = items.map((c) => ({
        id: c.id,
        name: `${c.first_name} ${c.last_name ?? ""}`.trim(),
        kind: "contact" as const,
        subtitle: c.companies?.[0]?.name,
      }));
    })();
  });
  // #task reference candidates (#197): host-scoped like the contact list — the task's project,
  // else its company, else the org's recent tasks — fetched lazily in the browser so the SSR
  // load pays nothing (docs/PERFORMANCE.md: meta=false&count=false skips discarded aggregates).
  let taskCandidates = $state<{ id: string; name: string; subtitle?: string }[]>([]);
  $effect(() => {
    const scope = task.project_id
      ? `&project_id=${task.project_id}`
      : task.company_id
        ? `&company_id=${task.company_id}`
        : "";
    void (async () => {
      const response = await fetch(`/api/v1/tasks?limit=200&meta=false&count=false${scope}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      interface TaskRow {
        id: string;
        title: string;
        status: string;
      }
      const items: TaskRow[] = (await response.json()).items ?? [];
      taskCandidates = items
        .filter((row) => row.id !== task.id)
        .map((row) => ({ id: row.id, name: row.title, subtitle: statusName(row.status) }));
    })();
  });
  const mentionCandidates = $derived([
    ...data.members.map((m) => ({
      id: m.user_id,
      name: m.full_name || m.email,
      kind: "user" as const,
    })),
    ...contactCandidates,
  ]);
  const priorities = ["low", "normal", "high"] as const;
  const freqs = ["daily", "weekly", "monthly", "quarterly", "yearly"] as const;

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const projectItems = $derived(data.projects.map((p) => ({ value: p.id, label: p.name })));
  const memberItems = $derived(
    data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
  const memberName = (id?: string | null) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? m.full_name || m.email : null;
  };
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name;
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name;

  // Two modes (docs/UX.md §3). "Use" (default) is working the task: change status, tick and
  // quick-add checklist items, comment, plan, open what's attached. "Edit" (⋯ menu, staff only)
  // is changing what the task *is*: title, description, relations, due/priority, labels,
  // recurrence, checklist structure, links and file attachments. Empty structural sections
  // don't render in use mode at all — their create forms live behind the pencil.
  // Arriving with the `?edit=1` marker (#78; a fresh create lands here with it, #230) opens
  // edit mode once — never for a portal login, whose surface is use-only.
  let editMode = $state(editIntent() && !(page.data.user?.isPortal ?? false));
  let confirmDelete = $state(false);
  // Inline create from the relation pickers (#115, docs/UX.md — per-picker definition of
  // done): the dialog posts to ?/createCompany / ?/createProject and the new record
  // auto-selects in the picker that asked. The created ids reset on the edit-mode toggle so
  // a stale pick never overrides the stored relation on a later edit session.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let createdCompanyId = $state("");
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  let createdProjectId = $state("");
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") createdCompanyId = created.id;
    if (created?.slot === "project") createdProjectId = created.id;
  });
  let editingCommentId = $state<string | null>(null);
  // Inline description editing for a checklist / a checklist item (issue #66), one at a time.
  let editingChecklistId = $state<string | null>(null);
  let editingItemId = $state<string | null>(null);
  // Bumped after a comment is posted to remount (and so clear) the markdown editor.
  let newCommentKey = $state(0);

  // One shared confirm for every inline sub-item delete (comment, checklist, item, link):
  // the ⋯ Delete sets the action/fields/message, then opens the dialog which owns the form.
  let subConfirmOpen = $state(false);
  let subConfirm = $state<{ action: string; fields: Record<string, string>; message: string }>({
    action: "",
    fields: {},
    message: "",
  });
  function askDelete(action: string, fields: Record<string, string>, message: string) {
    subConfirm = { action, fields, message };
    subConfirmOpen = true;
  }
  let showLabelPicker = $state(false);
  let newLabelColor = $state("blue");

  // Extending a deadline requires a reason (accountability): staged here, posted with the
  // single save (the API rejects an extension without one).
  let reasonModalOpen = $state(false);
  let stagedDueDate = $state("");
  let dueReason = $state("");
  let reasonDraft = $state("");
  function onDueChanged(value: string) {
    if (task.due_date && value && value > task.due_date) {
      stagedDueDate = value;
      reasonDraft = dueReason;
      reasonModalOpen = true;
    }
  }

  const today = new Date().toISOString().slice(0, 10);
  const overdue = $derived(!isDone && !!task.due_date && task.due_date < today);
  const currentLabelIds = $derived((task.labels ?? []).map((l) => l.id));

  // Time budget: logged vs allocated drives the colour (green → amber → red).
  const budgetPct = $derived(
    task.allocated_minutes ? (task.logged_minutes / task.allocated_minutes) * 100 : null,
  );
  const budgetColor = $derived(
    budgetPct == null
      ? ""
      : budgetPct >= 100
        ? "bg-red-500"
        : budgetPct >= 75
          ? "bg-amber-500"
          : "bg-green-500",
  );

  const when = (iso: string) => fmtDateTime(iso);

  /**
   * Who a stored row is attributed to (issue #64).
   *
   * A name with no live account is someone who has since been deleted — say so, rather than
   * handing their work to "System" (which is what a NULL actor used to mean here, and still
   * means when the recurrence cron writes the row). No name at all really is the system.
   */
  function actorLabel(a: { actor_name?: string | null; actor_deleted?: boolean }): string {
    if (!a.actor_name) return t("tasks.activity.system");
    return a.actor_deleted ? t("common.deleted_user", { name: a.actor_name }) : a.actor_name;
  }

  /** Same rule for a comment's author, whose absence used to render as a bare “—”. */
  function authorLabel(c: { author_name?: string | null; author_deleted?: boolean }): string {
    if (!c.author_name) return "—";
    return c.author_deleted ? t("common.deleted_user", { name: c.author_name }) : c.author_name;
  }

  /** Where an activity entry deep-links: a comment (`#comment-…`), or the contact moment a close
   *  was justified with (#157) — the interactions panel row carries `#interaction-…`. */
  function activityHref(a: { payload: Record<string, unknown> }): string | null {
    const commentId = a.payload.comment_id ? String(a.payload.comment_id) : null;
    if (commentId) {
      return (task.comments ?? []).some((c) => c.id === commentId) ? `#comment-${commentId}` : null;
    }
    if (a.payload.closing_interaction_id) return `#interaction-${a.payload.closing_interaction_id}`;
    // A mirrored contact-moment milestone (#152) links to the moment in the interactions panel.
    if (a.payload.interaction_id) return `#interaction-${a.payload.interaction_id}`;
    return null;
  }

  function activityText(a: { action: string; payload: Record<string, unknown> }): string {
    if (a.action === "status_changed") {
      // Statuses are tenant data now (issue #62): name them from the configured list, not an i18n
      // key. A status deleted since the change falls back to the stored key.
      // A close designated a contact moment (#157): say it was *afgerond met* that moment, not
      // only that the status moved — the trail must record what justified the close.
      if (a.payload.closing_subject) {
        return t("tasks.activity.status_closed_with_interaction", {
          from: statusName(String(a.payload.from)),
          to: statusName(String(a.payload.to)),
          subject: String(a.payload.closing_subject),
        });
      }
      return t("tasks.activity.status_changed", {
        from: statusName(String(a.payload.from)),
        to: statusName(String(a.payload.to)),
      });
    }
    if (a.action === "due_extended") {
      return t("tasks.activity.due_extended", {
        to: a.payload.to ? fmtDayMonth(String(a.payload.to)) : "—",
        reason: String(a.payload.reason ?? ""),
      });
    }
    if (a.action === "updated") {
      const names: Record<string, string> = {
        assignee_user_id: "assignee",
        company_id: "company",
        project_id: "project",
        allocated_minutes: "allocated",
      };
      const changed = (a.payload.changed as string[] | undefined) ?? [];
      const fields = changed.map((f) => t(`tasks.field.${names[f] ?? f}`)).join(", ");
      return t("tasks.activity.updated", { fields });
    }
    if (a.action === "checklist_renamed" || a.action === "checklist_item_renamed") {
      return t(`tasks.activity.${a.action}`, {
        from: String(a.payload.from ?? ""),
        to: String(a.payload.to ?? ""),
      });
    }
    if (a.action === "attachment_added" || a.action === "attachment_deleted") {
      return t(`tasks.activity.${a.action}`, { filename: String(a.payload.filename ?? "") });
    }
    // A contact-moment milestone mirrored onto this task from the core log (#152) — reuse the
    // shared activity.action.interaction.* strings the company/project panels already read.
    if (a.action.startsWith("interaction.")) {
      return t(`activity.action.${a.action}`, {
        kind: t(`interactions.kind.${String(a.payload.kind ?? "note")}`),
        subject: String(a.payload.subject ?? ""),
      });
    }
    if (
      a.action === "link_deleted" ||
      a.action === "checklist_created" ||
      a.action === "checklist_deleted" ||
      a.action === "checklist_item_added" ||
      a.action === "checklist_item_completed" ||
      a.action === "checklist_item_reopened" ||
      a.action === "checklist_item_deleted"
    ) {
      return t(`tasks.activity.${a.action}`, { title: String(a.payload.title ?? "") });
    }
    // Comment rows carry an excerpt of what was said; rows written before they did fall back
    // to the bare verb rather than quoting an empty string (#61).
    if (a.payload.excerpt) {
      return t(`tasks.activity.${a.action}_excerpt`, { excerpt: String(a.payload.excerpt) });
    }
    return t(`tasks.activity.${a.action}`);
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(task.title)}</title>
</svelte:head>

<div class="mb-4"></div>

<!-- Phone vs desktop order: a flex column below `lg` puts the details card (status, assignee,
     due date) straight after the title — on a phone those are what you came to change, and they
     must not live below the whole comment thread. At `lg` the grid takes over untouched. -->
<div class="flex flex-col gap-4 lg:grid lg:grid-cols-[1fr_320px]">
  <!-- Main column. `min-w-0` for the same reason the shell needs it (issue #36): a grid item's
       automatic minimum size is its content's min-content width, so without it the widest card
       inside dictates the column's width and the page grows past the viewport. -->
  <div class="order-1 min-w-0 space-y-4 lg:order-none lg:col-start-1 lg:row-start-1">
    <!-- Title + mode menu -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex items-start gap-3">
        {#if editMode}
          <input
            name="title"
            value={task.title}
            required
            form="task-edit"
            class="w-full flex-1 rounded-lg border border-border p-2 text-lg font-semibold text-text outline-none focus:border-brand"
          />
        {:else}
          <h1
            class="flex-1 text-lg font-semibold {isDone
              ? 'text-text-muted line-through'
              : 'text-text'}"
          >
            {task.title}
          </h1>
        {/if}

        {#if !isPortal}
          <!-- A portal contact works the task (read, comment) — never its definition. -->
          <ActionsMenu
            items={[
              {
                label: editMode ? t("tasks.detail.done_editing") : t("common.edit"),
                icon: Pencil,
                onclick: () => {
                  createdCompanyId = "";
                  createdProjectId = "";
                  editMode = !editMode;
                },
              },
              {
                label: t("tasks.detail.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => (confirmDelete = true),
              },
            ]}
          />
        {/if}
      </div>

      <div class="mt-2 flex flex-wrap items-center gap-2">
        {#each task.labels ?? [] as label (label.id)}
          <span
            class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
            >{label.name}</span
          >
        {/each}
        {#if overdue}
          <span
            class="rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-600 dark:bg-red-950 dark:text-red-400"
            >{t("tasks.due.overdue")}</span
          >
        {/if}
        {#if task.recurrence}
          <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted">
            ↻ {t(`tasks.recurrence.freq.${task.recurrence.freq}`)}
          </span>
        {/if}
        {#if editMode}
          <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
            >{t("tasks.detail.edit_mode")}</span
          >
        {/if}
      </div>

      <!-- Description -->
      <div class="mt-4 border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("tasks.field.description")}
        </h3>
        {#if editMode}
          <RichTextEditor
            name="description"
            form="task-edit"
            rows={4}
            value={task.description ?? ""}
          />
        {:else if task.description}
          <Markdown value={task.description} />
        {:else}
          <p class="text-sm text-text-muted">{t("tasks.detail.description_placeholder")}</p>
        {/if}
      </div>
    </section>
  </div>

  <!-- The rest of the main column — after the details card on a phone (order-3), back into the
       left grid column at `lg`. -->
  <div class="order-3 min-w-0 space-y-4 lg:order-none lg:col-start-1 lg:row-start-2">
    <!-- Planned blocks on the calendar (#188) — schedule, move, and log time from a passed one. -->
    <TaskSchedulePanel
      schedules={data.schedules}
      task={{
        id: task.id,
        title: task.title,
        project_id: task.project_id,
        company_id: task.company_id,
        assignee_user_id: task.assignee_user_id,
        allocated_minutes: task.allocated_minutes,
        due_date: task.due_date,
      }}
      members={data.members}
      currentUserId={page.data.user?.id ?? ""}
      canWrite={can(page.data.user, "tasks.schedule.write")}
      canScheduleAny={can(page.data.user, "tasks.schedule.write", "any")}
    />

    <!-- Checklists. Ticking and quick-adding items is "using" (docs/UX.md §3, §5); creating,
         renaming or deleting a checklist is structure and lives in edit mode. A task without
         checklists shows no section at all until you edit — an empty card with a create form
         is exactly the clutter use mode exists to avoid. -->
    {#if (task.checklists ?? []).length > 0 || editMode}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("tasks.checklist.title")}
        </h3>

        {#each task.checklists ?? [] as checklist (checklist.id)}
          {@const items = checklist.items ?? []}
          {@const total = items.length}
          {@const doneCount = items.filter((i) => i.done).length}
          <div class="mb-4">
            <div class="mb-1 flex items-center justify-between gap-2">
              <h4 class="text-sm font-semibold text-text">{checklist.title}</h4>
              <div class="flex items-center gap-2">
                <span class="text-xs tabular-nums text-text-muted"
                  >{t("tasks.checklist.progress", { done: doneCount, total })}</span
                >
                {#if editMode && items.length > 0}
                  <form method="POST" action="?/saveChecklistTemplate" use:enhance>
                    <input type="hidden" name="title" value={checklist.title} />
                    <!-- Item titles *and* descriptions, so the saved template carries both (issue #66). -->
                    <input
                      type="hidden"
                      name="items"
                      value={JSON.stringify(
                        items.map((i) => ({ title: i.title, description: i.description ?? null })),
                      )}
                    />
                    <button
                      class="text-xs text-text-muted hover:text-brand"
                      title={t("tasks.checklist.save_template_hint")}
                    >
                      {t("tasks.checklist.save_template")}
                    </button>
                  </form>
                {/if}
                {#if editMode}
                  <ActionsMenu
                    compact
                    items={[
                      {
                        label: t("common.edit"),
                        icon: Pencil,
                        onclick: () =>
                          (editingChecklistId =
                            editingChecklistId === checklist.id ? null : checklist.id),
                      },
                      {
                        label: t("common.delete"),
                        icon: Trash2,
                        danger: true,
                        onclick: () =>
                          askDelete(
                            "?/deleteChecklist",
                            { checklist_id: checklist.id },
                            t("tasks.checklist.delete_confirm"),
                          ),
                      },
                    ]}
                  />
                {/if}
              </div>
            </div>
            {#if editingChecklistId === checklist.id}
              <form
                method="POST"
                action="?/editChecklist"
                use:enhance={() =>
                  ({ update }) => {
                    editingChecklistId = null;
                    void update();
                  }}
                class="mb-2 space-y-2"
              >
                <input type="hidden" name="checklist_id" value={checklist.id} />
                <input name="title" value={checklist.title} required class={inputClass} />
                <RichTextEditor
                  name="description"
                  rows={2}
                  value={checklist.description ?? ""}
                  placeholder={t("tasks.checklist.description_placeholder")}
                />
                <div class="flex gap-2">
                  <button class="rounded-lg bg-brand px-2 py-1 text-xs font-medium text-white"
                    >{t("common.save")}</button
                  >
                  <button
                    type="button"
                    class="rounded-lg border border-border px-2 py-1 text-xs"
                    onclick={() => (editingChecklistId = null)}>{t("common.cancel")}</button
                  >
                </div>
              </form>
            {:else if checklist.description}
              <div class="mb-2"><Markdown value={checklist.description} /></div>
            {/if}
            {#if total > 0}
              <div class="mb-2 h-1.5 overflow-hidden rounded-full bg-surface">
                <div
                  class="h-full rounded-full {doneCount === total ? 'bg-green-500' : 'bg-brand'}"
                  style="width: {total ? Math.round((doneCount / total) * 100) : 0}%"
                ></div>
              </div>
            {/if}
            <ul class="space-y-1">
              {#each items as item (item.id)}
                <li class="group">
                  <div class="flex items-center gap-2">
                    <form
                      method="POST"
                      action="?/toggleItem"
                      use:enhance={() => {
                        // Snapshot before the server flips it: checking the last open to-do on an
                        // unfinished task opens the finish prompt after the reload.
                        const completesLast =
                          !item.done &&
                          openItemCount === 1 &&
                          !isDone &&
                          !isPortal &&
                          finishStatus !== null;
                        return ({ update }) => {
                          void update().then(() => {
                            if (completesLast) showFinishPrompt = true;
                          });
                        };
                      }}
                    >
                      <input type="hidden" name="checklist_id" value={checklist.id} />
                      <input type="hidden" name="item_id" value={item.id} />
                      <input type="hidden" name="done" value={String(!item.done)} />
                      <button
                        class="flex h-4 w-4 items-center justify-center rounded border text-[10px]
                        {item.done
                          ? 'border-brand bg-brand text-white'
                          : 'border-border text-transparent hover:border-brand'}"
                        aria-label={t("tasks.toggle_done")}>✓</button
                      >
                    </form>
                    <span
                      class="flex-1 text-sm {item.done
                        ? 'text-text-muted line-through'
                        : 'text-text'}">{item.title}</span
                    >
                    {#if editMode}
                      <ActionsMenu
                        compact
                        items={[
                          {
                            label: t("common.edit"),
                            icon: Pencil,
                            onclick: () =>
                              (editingItemId = editingItemId === item.id ? null : item.id),
                          },
                          {
                            label: t("common.delete"),
                            icon: Trash2,
                            danger: true,
                            onclick: () =>
                              askDelete(
                                "?/deleteItem",
                                { checklist_id: checklist.id, item_id: item.id },
                                t("tasks.checklist.item_delete_confirm"),
                              ),
                          },
                        ]}
                      />
                    {/if}
                  </div>
                  {#if editingItemId === item.id}
                    <form
                      method="POST"
                      action="?/editItem"
                      use:enhance={() =>
                        ({ update }) => {
                          editingItemId = null;
                          void update();
                        }}
                      class="mt-1 space-y-2 pl-6"
                    >
                      <input type="hidden" name="checklist_id" value={checklist.id} />
                      <input type="hidden" name="item_id" value={item.id} />
                      <input name="title" value={item.title} required class={inputClass} />
                      <RichTextEditor
                        name="description"
                        rows={2}
                        value={item.description ?? ""}
                        placeholder={t("tasks.checklist.description_placeholder")}
                      />
                      <div class="flex gap-2">
                        <button class="rounded-lg bg-brand px-2 py-1 text-xs font-medium text-white"
                          >{t("common.save")}</button
                        >
                        <button
                          type="button"
                          class="rounded-lg border border-border px-2 py-1 text-xs"
                          onclick={() => (editingItemId = null)}>{t("common.cancel")}</button
                        >
                      </div>
                    </form>
                  {:else if item.description}
                    <div class="mt-0.5 pl-6"><Markdown value={item.description} /></div>
                  {/if}
                </li>
              {/each}
            </ul>
            <form method="POST" action="?/addItem" use:enhance class="mt-2 flex gap-2">
              <input type="hidden" name="checklist_id" value={checklist.id} />
              <input
                name="title"
                placeholder={t("tasks.checklist.item_placeholder")}
                required
                class="min-w-0 flex-1 rounded-lg border border-border px-2 py-1 text-sm outline-none focus:border-brand"
              />
              <button
                class="rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:border-brand hover:text-brand"
                >＋</button
              >
            </form>
          </div>
        {/each}

        {#if editMode}
          <form method="POST" action="?/addChecklist" use:enhance class="flex gap-2">
            <!-- `min-w-0`: a flex `<input>` keeps its browser-default width (~228px here) as its
               min-content floor, so `flex-1` alone cannot shrink it and the row pushed the whole
               card past a phone's width (issue #36). -->
            <input
              name="title"
              placeholder={t("tasks.checklist.add")}
              required
              class="min-w-0 flex-1 rounded-lg border border-dashed border-border px-3 py-1.5 text-sm outline-none focus:border-brand"
            />
            <button
              class="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand"
            >
              {t("common.create")}
            </button>
          </form>
          {#if data.checklistTemplates.length > 0}
            <form method="POST" action="?/addChecklist" use:enhance class="mt-2 flex gap-2">
              <select
                name="template_id"
                required
                class="flex-1 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted"
              >
                {#each data.checklistTemplates as checklistTemplate (checklistTemplate.id)}
                  <option value={checklistTemplate.id}>
                    {checklistTemplate.title} ({checklistTemplate.items?.length ?? 0})
                  </option>
                {/each}
              </select>
              <button
                class="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand"
              >
                {t("tasks.checklist.from_template")}
              </button>
            </form>
          {/if}
        {/if}
      </section>
    {/if}

    <!-- Links & attachments. Use mode shows what is attached (open, download); adding a link,
         uploading a file and deleting either are edit-mode work (docs/UX.md §3). No links and
         no files → no section, until you edit. -->
    {#if (task.links ?? []).length > 0 || data.files.length > 0 || editMode}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("tasks.links.title")}
        </h3>
        {#if (task.links ?? []).length === 0}
          {#if editMode}<p class="mb-3 text-sm text-text-muted">{t("tasks.links.empty")}</p>{/if}
        {:else}
          <ul class="mb-3 space-y-1">
            {#each task.links ?? [] as link (link.id)}
              <li class="group flex items-center gap-2">
                <LinkIcon size={14} class="shrink-0 text-text-muted" />
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  class="min-w-0 flex-1 truncate text-sm text-brand hover:underline"
                >
                  {link.title || link.url}
                </a>
                {#if editMode}
                  <ActionsMenu
                    compact
                    items={[
                      {
                        label: t("common.delete"),
                        icon: Trash2,
                        danger: true,
                        onclick: () =>
                          askDelete(
                            "?/deleteLink",
                            { link_id: link.id },
                            t("tasks.links.delete_confirm"),
                          ),
                      },
                    ]}
                  />
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
        {#if editMode}
          <form
            method="POST"
            action="?/addLink"
            use:enhance={() =>
              ({ update }) =>
                void update({ reset: true })}
            class="flex flex-wrap gap-2"
          >
            <input
              name="url"
              required
              placeholder={t("tasks.links.url_placeholder")}
              class="min-w-[12rem] flex-1 rounded-lg border border-border px-3 py-1.5 text-sm outline-none focus:border-brand"
            />
            <input
              name="title"
              placeholder={t("tasks.links.title_placeholder")}
              class="w-40 rounded-lg border border-border px-3 py-1.5 text-sm outline-none focus:border-brand"
            />
            <button
              class="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand"
            >
              {t("common.create")}
            </button>
          </form>
        {/if}

        {#if !isPortal && (data.files.length > 0 || editMode)}
          <!-- Document uploads through the storage core (#123) — staff-only surface. -->
          <div class={editMode ? "mt-4 border-t border-border pt-4" : ""}>
            <FileAttachments
              files={data.files}
              uploadAction="?/uploadFile"
              deleteAction="?/deleteFile"
              error={form?.fileError ?? null}
              readonly={!editMode}
            />
          </div>
          {#if editMode}
            <p class="mt-2 text-[11px] text-text-muted">{t("tasks.links.files_hint")}</p>
          {/if}
        {/if}
      </section>
    {/if}

    <!-- Comments -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("tasks.comments.title")}
      </h3>

      <form
        method="POST"
        action="?/addComment"
        use:enhance={() =>
          ({ update, result }) => {
            // Reset the editor by remounting it; its internal state survives a plain form reset.
            if (result.type === "success") newCommentKey += 1;
            void update({ reset: true });
          }}
        class="mb-4"
      >
        {#key newCommentKey}
          <RichTextEditor
            name="body"
            rows={2}
            required
            placeholder={t("tasks.comments.placeholder")}
            mentions={mentionCandidates}
            tasks={taskCandidates}
          />
        {/key}
        <div class="mt-2 flex justify-end">
          <button
            class="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
            >{t("tasks.comments.send")}</button
          >
        </div>
      </form>

      {#if (task.comments ?? []).length === 0}
        <p class="text-sm text-text-muted">{t("tasks.comments.empty")}</p>
      {:else}
        <ul class="space-y-3">
          {#each task.comments ?? [] as comment (comment.id)}
            {@const canEditComment = comment.author_user_id === userId}
            {@const canDeleteComment = canEditComment || canDeleteAnyComment}
            <li id="comment-{comment.id}" class="rounded-lg border border-border bg-surface/50 p-3">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="text-xs font-semibold text-text">{authorLabel(comment)}</span>
                <div class="flex items-center gap-1 text-[11px] text-text-muted">
                  <span>{when(comment.created_at)}</span>
                  {#if comment.edited_at}<span>({t("tasks.comments.edited")})</span>{/if}
                  {#if canDeleteComment}
                    <ActionsMenu
                      compact
                      items={[
                        ...(canEditComment
                          ? [
                              {
                                label: t("common.edit"),
                                icon: Pencil,
                                onclick: () =>
                                  (editingCommentId =
                                    editingCommentId === comment.id ? null : comment.id),
                              },
                            ]
                          : []),
                        {
                          label: t("common.delete"),
                          icon: Trash2,
                          danger: true,
                          onclick: () =>
                            askDelete(
                              "?/deleteComment",
                              { comment_id: comment.id },
                              t("tasks.comments.delete_confirm"),
                            ),
                        },
                      ]}
                    />
                  {/if}
                </div>
              </div>
              {#if editingCommentId === comment.id}
                <form
                  method="POST"
                  action="?/editComment"
                  use:enhance={() =>
                    ({ update }) => {
                      editingCommentId = null;
                      void update();
                    }}
                >
                  <input type="hidden" name="comment_id" value={comment.id} />
                  <RichTextEditor
                    name="body"
                    rows={2}
                    required
                    value={comment.body}
                    mentions={mentionCandidates}
                    tasks={taskCandidates}
                  />
                  <div class="mt-1 flex gap-2">
                    <button class="rounded-lg bg-brand px-2 py-1 text-xs font-medium text-white"
                      >{t("common.save")}</button
                    >
                    <button
                      type="button"
                      class="rounded-lg border border-border px-2 py-1 text-xs"
                      onclick={() => (editingCommentId = null)}>{t("common.cancel")}</button
                    >
                  </div>
                </form>
              {:else}
                <Markdown value={comment.body} />
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- Panels contributed by enabled modules; history stays last (docs/UX.md). -->
    {#each isPortal ? [] : data.panels as panel (panel.key)}
      {@const PanelComponent = panelComponent(panel.key)}
      {#if PanelComponent}
        <section class="rounded-xl border border-border bg-surface-raised p-5">
          <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
            {t(panel.titleKey)}
          </h3>
          <PanelComponent data={panel.data} context={data.context} lookups={panelLookups} />
        </section>
      {/if}
    {/each}

    <!-- Activity — the staff paper trail, never a portal surface. -->
    {#if !isPortal}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("tasks.activity.title")}
        </h3>
        {#if activities.length === 0}
          <p class="text-sm text-text-muted">—</p>
        {:else}
          <ul class="space-y-2">
            {#each visibleActivities as activity (activity.id)}
              {@const href = activityHref(activity)}
              <li class="flex items-baseline gap-2 text-sm">
                <span class="shrink-0 text-[11px] tabular-nums text-text-muted"
                  >{when(activity.created_at)}</span
                >
                <span class="text-text">
                  <span class="font-medium">{actorLabel(activity)}</span>
                  {#if href}
                    <a class="hover:text-brand hover:underline" {href}>{activityText(activity)}</a>
                  {:else}
                    {activityText(activity)}
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
          {#if activities.length > ACTIVITY_COLLAPSED}
            <button
              type="button"
              class="mt-3 text-xs font-medium text-brand hover:underline"
              onclick={() => (activityExpanded = !activityExpanded)}
            >
              {activityExpanded
                ? t("common.show_less")
                : t("common.show_all", { count: activities.length })}
            </button>
          {/if}
        {/if}
      </section>
    {/if}
  </div>

  <!-- Sidebar — second on a phone (order-2, right under the title), right column at `lg`. -->
  <aside
    class="order-2 min-w-0 space-y-4 lg:order-none lg:col-start-2 lg:row-span-2 lg:row-start-1"
  >
    <section class="rounded-xl border border-border bg-surface-raised p-4">
      <div class="space-y-3">
        <!-- Status is core workflow → always editable for staff; a portal contact reads it. -->
        <div>
          <label for="status" class="mb-1 block text-xs font-medium text-text-muted"
            >{t("tasks.field.status")}</label
          >
          {#if isPortal}
            <p id="status" class="text-sm text-text">
              {statuses.find((s) => s.key === task.status)?.name ?? task.status}
            </p>
          {:else}
            <form method="POST" action="?/update" use:enhance>
              <select
                id="status"
                name="status"
                class={inputClass}
                onchange={(e) => e.currentTarget.form?.requestSubmit()}
              >
                {#each statuses as s (s.key)}
                  <option value={s.key} selected={task.status === s.key}>{s.name}</option>
                {/each}
              </select>
            </form>
          {/if}
        </div>

        <!-- Time budget -->
        {#if task.allocated_minutes || task.logged_minutes}
          <div>
            <div class="mb-1 flex items-center justify-between text-xs">
              <span class="font-medium text-text-muted">{t("tasks.field.allocated")}</span>
              <span class="tabular-nums text-text">
                {formatMinutes(task.logged_minutes)}{#if task.allocated_minutes}&nbsp;/ {formatMinutes(
                    task.allocated_minutes,
                  )}{/if}
              </span>
            </div>
            {#if budgetPct != null}
              <div class="h-2 overflow-hidden rounded-full bg-surface">
                <div
                  class="h-full rounded-full {budgetColor}"
                  style="width: {Math.min(100, budgetPct)}%"
                ></div>
              </div>
              {#if task.allocated_minutes}
                <p
                  class="mt-1 text-[11px] {budgetPct >= 100
                    ? 'text-red-600 dark:text-red-400'
                    : 'text-text-muted'}"
                >
                  {budgetPct >= 100
                    ? t("tasks.budget.over", {
                        amount: formatMinutes(task.logged_minutes - task.allocated_minutes),
                      })
                    : t("tasks.budget.left", {
                        amount: formatMinutes(task.allocated_minutes - task.logged_minutes),
                      })}
                </p>
              {/if}
            {/if}
          </div>
        {/if}

        {#if editMode}
          <div>
            <label for="allocated" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.allocated_input")}</label
            >
            <input
              id="allocated"
              name="allocated_minutes"
              type="number"
              min="0"
              step="15"
              form="task-edit"
              value={task.allocated_minutes ?? ""}
              class={inputClass}
            />
          </div>
          <div>
            <label for="priority" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.priority")}</label
            >
            <select id="priority" name="priority" form="task-edit" class={inputClass}>
              {#each priorities as p (p)}
                <option value={p} selected={task.priority === p}>{t(`tasks.priority.${p}`)}</option>
              {/each}
            </select>
          </div>
          <div>
            <label for="assignee" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.assignee")}</label
            >
            <Combobox
              items={memberItems}
              name="assignee_user_id"
              value={task.assignee_user_id ?? ""}
              id="assignee"
              formId="task-edit"
            />
          </div>
          <div>
            <label for="project" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.project")}</label
            >
            <Combobox
              items={projectItems}
              name="project_id"
              value={createdProjectId || (task.project_id ?? "")}
              id="project"
              formId="task-edit"
              oncreate={(name) => {
                qcProjectName = name;
                qcProjectOpen = true;
              }}
            />
          </div>
          <div>
            <label for="company" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.company")}</label
            >
            <Combobox
              items={companyItems}
              name="company_id"
              value={createdCompanyId || (task.company_id ?? "")}
              id="company"
              formId="task-edit"
              oncreate={(name) => {
                qcCompanyName = name;
                qcCompanyOpen = true;
              }}
            />
          </div>
          <div>
            <label for="due_date" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.due_date")}</label
            >
            <DateInput
              id="due_date"
              name="due_date"
              value={task.due_date ?? ""}
              formId="task-edit"
              onchange={onDueChanged}
            />
            <p class="mt-1 text-[11px] text-text-muted">{t("tasks.detail.due_reason_hint")}</p>
          </div>
          <div>
            <!-- Per-task close policy (#157 extended). Hidden "false" precedes the checkbox so an
                 unchecked box still submits a value; the status quick-form never carries it. -->
            <input type="hidden" name="requires_interaction" value="false" form="task-edit" />
            <label class="flex items-start gap-2 text-sm text-text">
              <FormCheckbox
                name="requires_interaction"
                value="true"
                checked={task.requires_interaction}
                form="task-edit"
                class="mt-0.5 shrink-0"
              />
              <span>
                <span class="font-medium">{t("tasks.field.requires_interaction")}</span>
                <span class="mt-0.5 block text-[11px] leading-snug text-text-muted"
                  >{t("tasks.field.requires_interaction_hint")}</span
                >
              </span>
            </label>
          </div>
          <div>
            <!-- Client-portal visibility: off by default, ticked per task by staff. -->
            <input type="hidden" name="visible_to_client" value="false" form="task-edit" />
            <label class="flex items-start gap-2 text-sm text-text">
              <FormCheckbox
                name="visible_to_client"
                value="true"
                checked={task.visible_to_client}
                form="task-edit"
                class="mt-0.5 shrink-0"
              />
              <span>
                <span class="font-medium">{t("tasks.field.visible_to_client")}</span>
                <span class="mt-0.5 block text-[11px] leading-snug text-text-muted"
                  >{t("tasks.field.visible_to_client_hint")}</span
                >
              </span>
            </label>
          </div>
        {:else}
          <!-- Use mode: compact read-only summary -->
          <dl class="space-y-2 text-sm">
            <div class="flex items-center justify-between gap-2">
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.assignee")}</dt>
              <dd class="text-text">{memberName(task.assignee_user_id) ?? "—"}</dd>
            </div>
            <div class="flex items-center justify-between gap-2">
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.project")}</dt>
              <dd class="truncate text-text">
                {#if task.project_id}
                  <a href={`/projects/${task.project_id}`} class="hover:text-brand"
                    >{projectName(task.project_id) ?? "—"}</a
                  >
                {:else}—{/if}
              </dd>
            </div>
            <div class="flex items-center justify-between gap-2">
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.company")}</dt>
              <dd class="truncate text-text">
                {#if task.company_id}
                  <a href={`/companies/${task.company_id}`} class="hover:text-brand"
                    >{companyName(task.company_id) ?? "—"}</a
                  >
                {:else}—{/if}
              </dd>
            </div>
            <div class="flex items-center justify-between gap-2">
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.due_date")}</dt>
              <dd
                class="tabular-nums {overdue
                  ? 'font-semibold text-red-600 dark:text-red-400'
                  : 'text-text'}"
              >
                {task.due_date ? fmtDayMonth(task.due_date) : "—"}
              </dd>
            </div>
            <div class="flex items-center justify-between gap-2">
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.priority")}</dt>
              <dd class="text-text">{t(`tasks.priority.${task.priority}`)}</dd>
            </div>
            {#if task.requires_interaction}
              <div class="flex items-center justify-between gap-2">
                <dt class="text-xs font-medium text-text-muted">
                  {t("tasks.field.requires_interaction")}
                </dt>
                <dd
                  class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                >
                  {t("tasks.field.requires_interaction_badge")}
                </dd>
              </div>
            {/if}
          </dl>
        {/if}
      </div>
    </section>

    <!-- Labels — edit-mode only: in use mode the chips already sit under the title, so a second
         card repeating them (or teaching "no labels yet") is noise (docs/UX.md §3). -->
    {#if editMode}
      <section class="rounded-xl border border-border bg-surface-raised p-4">
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
            {t("tasks.field.labels")}
          </h3>
          <button
            type="button"
            class="text-xs text-text-muted hover:text-brand"
            onclick={() => (showLabelPicker = !showLabelPicker)}
          >
            {showLabelPicker ? t("common.cancel") : t("common.edit")}
          </button>
        </div>

        {#if showLabelPicker}
          <form
            method="POST"
            action="?/setLabels"
            use:enhance={() =>
              ({ update }) => {
                showLabelPicker = false;
                void update();
              }}
            class="space-y-1"
          >
            {#each data.labels as label (label.id)}
              <label class="flex items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface">
                <FormCheckbox
                  name="label_ids"
                  value={label.id}
                  checked={currentLabelIds.includes(label.id)}
                  class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
                />
                <span class="h-2.5 w-2.5 rounded-full {labelDotClass(label.color)}"></span>
                <span class="text-text">{label.name}</span>
              </label>
            {/each}
            <button
              class="mt-2 w-full rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
              >{t("common.apply")}</button
            >
          </form>

          <form
            method="POST"
            action="?/createLabel"
            use:enhance={() =>
              ({ update }) => {
                showLabelPicker = false;
                void update();
              }}
            class="mt-3 border-t border-border pt-3"
          >
            {#each currentLabelIds as id (id)}
              <input type="hidden" name="current_label_ids" value={id} />
            {/each}
            <input
              name="name"
              placeholder={t("tasks.labels.new_placeholder")}
              required
              class="w-full rounded-lg border border-border px-2 py-1 text-sm"
            />
            <input type="hidden" name="color" value={newLabelColor} />
            <div class="mt-2 flex flex-wrap gap-1">
              {#each LABEL_COLORS as color (color)}
                <button
                  type="button"
                  aria-label={color}
                  class="h-5 w-5 rounded-full {labelDotClass(color)} {newLabelColor === color
                    ? 'ring-2 ring-text ring-offset-1'
                    : ''}"
                  onclick={() => (newLabelColor = color)}
                ></button>
              {/each}
            </div>
            <button
              class="mt-2 w-full rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand"
            >
              {t("tasks.labels.create")}
            </button>
          </form>
        {:else if (task.labels ?? []).length === 0}
          <p class="text-sm text-text-muted">{t("tasks.labels.empty")}</p>
        {:else}
          <div class="flex flex-wrap gap-1">
            {#each task.labels ?? [] as label (label.id)}
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(
                  label.color,
                )}">{label.name}</span
              >
            {/each}
          </div>
        {/if}
      </section>
    {/if}

    <!-- Recurrence (definition → edit mode only) -->
    {#if editMode}
      <section class="rounded-xl border border-border bg-surface-raised p-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("tasks.recurrence.title")}
        </h3>
        <div class="space-y-2">
          <select name="freq" form="task-edit" class={inputClass}>
            <option value="" selected={!task.recurrence}>{t("tasks.recurrence.none")}</option>
            {#each freqs as f (f)}
              <option value={f} selected={task.recurrence?.freq === f}
                >{t(`tasks.recurrence.freq.${f}`)}</option
              >
            {/each}
          </select>
          <div class="grid grid-cols-2 gap-2">
            <input
              name="interval"
              type="number"
              min="1"
              max="365"
              value={task.recurrence?.interval ?? 1}
              form="task-edit"
              class={inputClass}
              aria-label={t("tasks.recurrence.interval")}
            />
            <select
              name="mode"
              form="task-edit"
              class={inputClass}
              aria-label={t("tasks.recurrence.mode")}
            >
              <option value="after_completion" selected={task.recurrence?.mode !== "schedule"}>
                {t("tasks.recurrence.mode.after_completion")}
              </option>
              <option value="schedule" selected={task.recurrence?.mode === "schedule"}>
                {t("tasks.recurrence.mode.schedule")}
              </option>
            </select>
          </div>
          <p class="text-[11px] leading-snug text-text-muted">{t("tasks.recurrence.hint")}</p>
        </div>
      </section>

      <!-- The one save for the whole edit mode: inputs across the page join via form="task-edit". -->
      <form
        id="task-edit"
        method="POST"
        action="?/update"
        use:enhance={() =>
          ({ update }) => {
            editMode = false;
            dueReason = "";
            void update();
          }}
      >
        <input type="hidden" name="due_change_reason" value={dueReason} />
        <button
          class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </form>
    {/if}
  </aside>
</div>

{#if form?.error}
  <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<!-- Deadline extension requires a reason (logged in the activity feed) -->
<Modal bind:open={reasonModalOpen} title={t("tasks.detail.due_reason_title")}>
  <div class="space-y-3">
    <p class="text-sm text-text-muted">
      {t("tasks.detail.due_reason_body", {
        from: task.due_date ? fmtDayMonth(task.due_date) : "—",
        to: stagedDueDate ? fmtDayMonth(stagedDueDate) : "—",
      })}
    </p>
    <textarea
      rows="3"
      bind:value={reasonDraft}
      placeholder={t("tasks.detail.due_reason_placeholder")}
      class={inputClass}></textarea>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (reasonModalOpen = false)}>{t("common.cancel")}</button
      >
      <button
        type="button"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => {
          dueReason = reasonDraft.trim();
          reasonModalOpen = false;
        }}
      >
        {t("common.confirm")}
      </button>
    </div>
  </div>
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("tasks.detail.delete")}
  message={t("tasks.detail.delete_confirm")}
  action="?/delete"
/>

<!-- Shared confirm for inline sub-item deletes (comment / checklist / item / link) -->
<ConfirmDialog
  bind:open={subConfirmOpen}
  title={t("common.delete")}
  message={subConfirm.message}
  action={subConfirm.action}
  fields={subConfirm.fields}
/>

<!-- The last to-do was just ticked: offer to move the task along — or, when finishing is gated
     on a closing contact moment (#157), say exactly that instead of offering a doomed move. -->
<Modal bind:open={showFinishPrompt} title={t("tasks.finish_prompt.title")}>
  {#if finishNeedsMoment}
    <p class="text-sm text-text">{t("tasks.finish_prompt.needs_interaction")}</p>
    <div class="mt-4 flex justify-end">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (showFinishPrompt = false)}
      >
        {t("common.close")}
      </button>
    </div>
  {:else}
    <p class="text-sm text-text">
      {t("tasks.finish_prompt.message", { status: finishStatus?.name ?? "" })}
    </p>
    <div class="mt-4 flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (showFinishPrompt = false)}
      >
        {t("tasks.finish_prompt.not_now")}
      </button>
      <form
        method="POST"
        action="?/update"
        use:enhance={() =>
          ({ update }) => {
            showFinishPrompt = false;
            return update();
          }}
      >
        <input type="hidden" name="status" value={finishStatus?.key ?? ""} />
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("tasks.finish_prompt.confirm")}
        </button>
      </form>
    </div>
  {/if}
</Modal>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={data.companyDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

<!-- Inline project create from the edit surface's picker (docs/UX.md — per-picker rule). -->
<Modal bind:open={qcProjectOpen} title={t("time.quick_create.project")}>
  {#key qcProjectName + String(qcProjectOpen)}
    <form
      method="POST"
      action="?/createProject"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") qcProjectOpen = false;
          void update({ reset: false });
        }}
      class="space-y-3"
    >
      <div>
        <label for="qc-task-project-name" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.name")}</label
        >
        <input
          id="qc-task-project-name"
          name="name"
          value={qcProjectName}
          required
          class={inputClass}
        />
      </div>
      <div>
        <label for="qc-task-project-company" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.company")}</label
        >
        <Combobox
          items={companyItems}
          name="company_id"
          value={createdCompanyId || (task.company_id ?? "")}
          id="qc-task-project-company"
          placeholder={t("projects.field.company")}
        />
      </div>
      <div>
        <label for="qc-task-project-rate" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.hourly_rate")}</label
        >
        <input
          id="qc-task-project-rate"
          name="hourly_rate"
          type="number"
          min="0"
          step="0.01"
          class={inputClass}
        />
      </div>
      {#if form?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcProjectOpen = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.create")}</button
        >
      </div>
    </form>
  {/key}
</Modal>
