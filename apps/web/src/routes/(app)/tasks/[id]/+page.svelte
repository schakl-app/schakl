<script lang="ts">
  import { Link as LinkIcon, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime, fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";
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
    tasks: [],
  });

  // The activity log grows without bound on a busy task (issue #86): show the most recent few and
  // expand the rest in place. Rows are newest-first, so the head is the newest.
  const ACTIVITY_COLLAPSED = 3;
  let activityExpanded = $state(false);
  const activities = $derived(task.activities ?? []);
  const visibleActivities = $derived(
    activityExpanded || activities.length <= ACTIVITY_COLLAPSED
      ? activities
      : activities.slice(0, ACTIVITY_COLLAPSED),
  );
  const userId = $derived(page.data.user?.id ?? "");
  // `tasks.comment.write:any` lets a manager clean up anyone's comment; the author always can.
  const canDeleteAnyComment = $derived(can(page.data.user, "tasks.comment.write", "any"));

  // The org's configured status vocabulary (issue #62), from the /tasks layout load.
  const statuses = $derived(data.statuses);
  const statusName = (key: string) =>
    statuses.find((s) => s.key === key)?.name ?? key;
  const isDone = $derived(statuses.find((s) => s.key === task.status)?.is_terminal ?? false);
  // @mention candidates for the comment composer (issue #63): the org members already loaded.
  const mentionCandidates = $derived(
    data.members.map((m) => ({ id: m.user_id, name: m.full_name || m.email })),
  );
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

  // Two modes: "use" (default — work the task: tick items, comment, attach) and "edit"
  // (change its definition), toggled from the ⋯ menu.
  let editMode = $state(false);
  let confirmDelete = $state(false);
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

  /** Comments the feed can still link to; a deleted one keeps its excerpt but has nowhere to go. */
  function activityHref(a: { payload: Record<string, unknown> }): string | null {
    const id = a.payload.comment_id ? String(a.payload.comment_id) : null;
    if (!id) return null;
    return (task.comments ?? []).some((c) => c.id === id) ? `#comment-${id}` : null;
  }

  function activityText(a: { action: string; payload: Record<string, unknown> }): string {
    if (a.action === "status_changed") {
      // Statuses are tenant data now (issue #62): name them from the configured list, not an i18n
      // key. A status deleted since the change falls back to the stored key.
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

<div class="mb-4">
  <a href="/tasks" class="text-sm text-text-muted hover:text-text">← {t("tasks.title")}</a>
</div>

<div class="grid gap-4 lg:grid-cols-[1fr_320px]">
  <!-- Main column. `min-w-0` for the same reason the shell needs it (issue #36): a grid item's
       automatic minimum size is its content's min-content width, so without it the widest card
       inside dictates the column's width and the page grows past the viewport. -->
  <div class="min-w-0 space-y-4">
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

        <ActionsMenu
          items={[
            {
              label: editMode ? t("tasks.detail.done_editing") : t("common.edit"),
              icon: Pencil,
              onclick: () => (editMode = !editMode),
            },
            {
              label: t("tasks.detail.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => (confirmDelete = true),
            },
          ]}
        />
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

    <!-- Checklists (always interactive — ticking items is "using") -->
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
              {#if items.length > 0}
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
                  <form method="POST" action="?/toggleItem" use:enhance>
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
                  <ActionsMenu
                    compact
                    items={[
                      {
                        label: t("common.edit"),
                        icon: Pencil,
                        onclick: () => (editingItemId = editingItemId === item.id ? null : item.id),
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
    </section>

    <!-- Links (URL attachments) -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("tasks.links.title")}
      </h3>
      {#if (task.links ?? []).length === 0}
        <p class="mb-3 text-sm text-text-muted">{t("tasks.links.empty")}</p>
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
            </li>
          {/each}
        </ul>
      {/if}
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

      <!-- Document uploads through the storage core (#123). -->
      <div class="mt-4 border-t border-border pt-4">
        <FileAttachments
          files={data.files}
          uploadAction="?/uploadFile"
          deleteAction="?/deleteFile"
          error={form?.fileError ?? null}
        />
      </div>
      <p class="mt-2 text-[11px] text-text-muted">{t("tasks.links.files_hint")}</p>
    </section>

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
    {#each data.panels as panel (panel.key)}
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

    <!-- Activity -->
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
  </div>

  <!-- Sidebar -->
  <aside class="min-w-0 space-y-4">
    <section class="rounded-xl border border-border bg-surface-raised p-4">
      <div class="space-y-3">
        <!-- Status is core workflow → always editable -->
        <div>
          <label for="status" class="mb-1 block text-xs font-medium text-text-muted"
            >{t("tasks.field.status")}</label
          >
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
              value={task.project_id ?? ""}
              id="project"
              formId="task-edit"
            />
          </div>
          <div>
            <label for="company" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.company")}</label
            >
            <Combobox
              items={companyItems}
              name="company_id"
              value={task.company_id ?? ""}
              id="company"
              formId="task-edit"
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
          </dl>
        {/if}
      </div>
    </section>

    <!-- Labels -->
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
              <input
                type="checkbox"
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
              class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
              >{label.name}</span
            >
          {/each}
        </div>
      {/if}
    </section>

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
