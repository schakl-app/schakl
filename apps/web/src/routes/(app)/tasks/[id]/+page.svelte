<script lang="ts">
  import {
    ArrowDown,
    ArrowUp,
    Copy,
    GripVertical,
    Link as LinkIcon,
    Pencil,
    Reply,
    Trash2,
  } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { applyAction, enhance } from "$app/forms";
  import { page } from "$app/state";
  import { editIntent } from "$lib/core/edit-intent";
  import { fmtDateTime, fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskAssigneePicker from "$lib/modules/tasks/TaskAssigneePicker.svelte";
  import TaskSchedulePanel from "$lib/modules/tasks/TaskSchedulePanel.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import { entityPanelComponent } from "$lib/core/registry";

  let { data, form } = $props();

  const task = $derived(data.task);

  // Panels contributed by enabled modules (CLAUDE.md §6) — contactmomenten, Drive, and
  // whatever ships later, composed exactly like the project page does.
  const enabledModules = $derived(page.data.theme?.enabledModules ?? []);
  const panelComponent = (key: string) => entityPanelComponent(enabledModules, "task", key);
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
  // Ticking and quick-adding checklist items are "use mode" affordances that live outside edit
  // mode (docs/UX.md), but they are still task writes (the API gates the item PATCH/POST on
  // `tasks.task.write`). A read-only portal client (#244) reaches this page for a client-visible
  // task, so the controls mirror the API: shown to a writer, read-only for everyone else.
  //
  // `canWriteTask` refines by row (`:own` means assignee), which is the whole answer on a detail
  // page: this screen is *about* one record, so every write control on it — the ⋯ → Bewerken
  // included — asks about that record rather than about the module.
  const canEditTask = $derived(canWriteTask(page.data.user, task));
  // Deleting is its own, genuinely unscoped permission (admin by default): a `:own` assignee
  // may finish their task, never destroy it. It was ungated here — the ⋯ menu offered Bewerken
  // and Verwijderen to any staff viewer and let the API say no.
  const canDeleteTask = $derived(can(page.data.user, "tasks.task.delete"));
  // Commenting is a third key again, and the one thing a portal client *may* write (#193). It
  // gates answering a comment too (#312) — a reply is a comment, posted by the same route.
  const canComment = $derived(can(page.data.user, "tasks.comment.write"));
  // Attaching a document is the storage core's permission, not the task's — the same split the
  // project page already makes. Being allowed to edit the task is not being allowed to upload.
  const canWriteFile = $derived(can(page.data.user, "files.file.write"));
  // Saving a checklist into the org-wide repository is a *different* capability from editing this
  // task — its own permission, held by nobody by default but admin. Without this gate a member in
  // edit mode was offered "Als sjabloon opslaan" and got a 403 for their trouble.
  const canSaveChecklistTemplate = $derived(can(page.data.user, "tasks.checklist_template.write"));
  // Same story for the org's label vocabulary: applying labels to this task is a task write,
  // minting a new one is `tasks.label.write`.
  const canWriteLabels = $derived(can(page.data.user, "tasks.label.write"));

  // The org's configured status vocabulary (issue #62), from the /tasks layout load.
  const statuses = $derived(data.statuses);
  const statusName = (key: string) => statuses.find((s) => s.key === key)?.name ?? key;
  const isDone = $derived(statuses.find((s) => s.key === task.status)?.is_terminal ?? false);

  // Ticking the *last* open to-do offers to finish the task (the to-dos and the status should
  // not drift apart silently). If finishing is gated on a closing contact moment (#157 — the
  // task's own flag, or the terminal status's), the prompt says so instead of offering a move
  // that the API would refuse.
  let showFinishPrompt = $state(false);
  // `openItemCount` counts the rows the *screen* holds (`dndItems`, declared below) rather than
  // the ones the load returned: a tick is optimistic now, so the record is a round trip behind
  // the checkbox and counting it would arm the finish prompt one tick late.
  const finishStatus = $derived(statuses.find((s) => s.is_terminal) ?? null);
  const finishNeedsMoment = $derived(
    (task.requires_interaction || (finishStatus?.requires_interaction ?? false)) &&
      !task.closing_interaction_id,
  );
  // The @ and # candidate lists are the editor's own business (#237, #290). This page used to
  // fire two mount-time fetches — 200 contacts and 200 tasks — on *every* open, to fill
  // dropdowns most opens never trigger. `RichTextEditor` fetches them on first focus from the
  // shared TTL cache in `lib/core/richtext/candidates.ts`, scoped to this task's project/company,
  // so five editors on this page still cost one fetch and an untouched page costs none.
  const candidateScope = $derived({
    companyId: task.company_id ?? null,
    projectId: task.project_id ?? null,
  });

  const priorities = ["low", "normal", "high"] as const;
  const freqs = ["daily", "weekly", "monthly", "quarterly", "yearly"] as const;

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));

  // Live company/project picks for the edit form (#227): the client narrows the project list
  // and a picked project backfills its client, like every create-side pairing of these two
  // pickers (time's EntryForm, the interaction forms). Re-armed from the stored task on the
  // edit-mode toggle and when navigating to another task — a mid-session reload (a comment,
  // a quick-create) must not clobber a live pick.
  // svelte-ignore state_referenced_locally
  let fCompany = $state(task.company_id ?? "");
  // svelte-ignore state_referenced_locally
  let fProject = $state(task.project_id ?? "");
  // svelte-ignore state_referenced_locally
  let pickedTaskId = task.id;
  $effect(() => {
    if (task.id !== pickedTaskId) {
      pickedTaskId = task.id;
      fCompany = task.company_id ?? "";
      fProject = task.project_id ?? "";
    }
  });
  const projectItems = $derived(
    (fCompany
      ? data.projects.filter((p) => p.company_id === fCompany || !p.company_id)
      : data.projects
    ).map((p) => ({ value: p.id, label: p.name })),
  );
  function onCompanyPicked(id: string) {
    fCompany = id;
    // A selected project of another client drops out of the narrowed list yet would still be
    // posted by its hidden input — clear it instead (the API stores the pair as given).
    const project = data.projects.find((p) => p.id === fProject);
    if (id && project?.company_id && project.company_id !== id) fProject = "";
  }
  function onProjectPicked(id: string) {
    fProject = id;
    const project = data.projects.find((p) => p.id === id);
    if (project?.company_id) fCompany = project.company_id;
  }
  // The task's own client contacts (#273): the options for a contact assignee, and the source
  // for naming a contact assignee in the read view. Follows the *live* company pick (fCompany) so
  // re-homing the task in edit mode narrows the options.
  //
  // Fetched only when something actually needs it (#290): edit mode, where the picker is drawn,
  // or a task that already carries a contact assignee, whose name the read view has to show. It
  // used to piggyback on a mention-candidate fetch that *every* open paid for; that fetch is
  // gone, and this must not quietly reinstate it for the majority of tasks, which are assigned
  // to a colleague or to nobody.
  let editContacts = $state<{ id: string; name: string }[]>([]);
  let editContactsFor = $state<string>("");
  $effect(() => {
    const companyId = fCompany;
    if (!companyId) return;
    if (!editMode && !task.assignee_contact_id) return;
    if (companyId === editContactsFor) return;
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
      editContacts = rows.map((c) => ({
        id: c.id,
        name: `${c.first_name} ${c.last_name ?? ""}`.trim(),
      }));
      editContactsFor = companyId;
    })();
  });
  const assigneeContacts = $derived(fCompany && editContactsFor === fCompany ? editContacts : []);
  const contactName = (id?: string | null) =>
    id ? (assigneeContacts.find((c) => c.id === id)?.name ?? null) : null;

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
  // edit mode once — and only for someone who may actually edit *this* task. A URL is not a
  // grant: the ⋯ that sets the marker is gated, but a pasted link would otherwise open a form
  // whose every save 403s, for a portal login and for a `:own` holder on a colleague's task alike.
  // svelte-ignore state_referenced_locally
  let editMode = $state(editIntent() && canWriteTask(page.data.user, data.task));
  const busy = new InFlight();
  let confirmDelete = $state(false);
  // Inline create from the relation pickers (#115, docs/UX.md — per-picker definition of
  // done): the dialog posts to ?/createCompany / ?/createProject and the new record
  // auto-selects in the picker that asked, narrowing and backfilling like a manual pick.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.slot === "company") onCompanyPicked(created.id);
    if (created?.slot === "project") onProjectPicked(created.id);
  });
  let editingCommentId = $state<string | null>(null);
  // Inline description editing for a checklist / a checklist item (issue #66), one at a time.
  let editingChecklistId = $state<string | null>(null);
  let editingItemId = $state<string | null>(null);

  // ---------------------------------------------------------------------------------------- //
  // Reordering checklists, and items inside one (edit mode)
  //
  // Order is structure, so it lives behind the pencil beside rename and delete — a to-do you
  // dragged by accident while ticking it off is a change you did not ask for.
  //
  // Both gestures produce the same thing — the whole new order — and post it as one call, so a
  // drag can never half-apply. Drag *and* arrows, because a drag is the only reorder a mouse
  // wants and the arrows are the only one a keyboard or a screen reader has (docs/UX.md, the same
  // pairing `ColumnPicker` and Instellingen → Dashboard already ship).
  // ---------------------------------------------------------------------------------------- //
  type ChecklistRow = NonNullable<typeof task.checklists>[number];
  type ItemRow = NonNullable<ChecklistRow["items"]>[number];

  // The arrays the drag zones own and mutate mid-gesture, **initialised inline and re-armed by an
  // effect** — both halves are load-bearing, and each one alone ships a bug:
  //   · a `$state([])` filled only by an `$effect` server-renders an empty section, because an
  //     effect does not run on the server: every checklist appeared a frame after hydration;
  //   · a writable `$derived` (`ColumnPicker`'s recipe) renders on the server but hands
  //     `svelte-dnd-action` an array it does not own, and the drag never starts at all.
  // The effect re-arms from the record on every load, so a saved order — or a colleague's edit —
  // wins over the dragged-in-flight one.
  // svelte-ignore state_referenced_locally
  let dndChecklists = $state<ChecklistRow[]>([...(task.checklists ?? [])]);
  // svelte-ignore state_referenced_locally
  let dndItems = $state<Record<string, ItemRow[]>>(
    Object.fromEntries((task.checklists ?? []).map((cl) => [cl.id, [...(cl.items ?? [])]])),
  );
  $effect(() => {
    const lists = task.checklists ?? [];
    dndChecklists = [...lists];
    dndItems = Object.fromEntries(lists.map((cl) => [cl.id, [...(cl.items ?? [])]]));
  });

  /** Open to-dos across every list, counted off the rows on screen (see `showFinishPrompt`). */
  const openItemCount = $derived(
    Object.values(dndItems).reduce((n, items) => n + items.filter((i) => !i.done).length, 0),
  );

  // The zones stay disabled until a grip takes the pointer down — the rows hold checkboxes,
  // menus and (while editing) text inputs, and a drag that starts anywhere would eat all three.
  // Two flags, not one: pressing an item's grip must not also arm the checklist zone around it.
  let dragChecklists = $state(false);
  let dragItemsIn = $state<string | null>(null);

  let checklistOrderForm: HTMLFormElement | undefined = $state();
  let itemOrderForm: HTMLFormElement | undefined = $state();
  let orderIds = $state("");
  let orderChecklistId = $state("");

  // Only ids the *record* still has: mid-drag the dnd zone inserts a shadow placeholder whose id
  // is not a row's, and a list deleted in another tab is gone. Either would 404 the whole call.
  function realIds(ids: string[], known: { id: string }[]): string[] {
    return ids.filter((id) => known.some((row) => row.id === id));
  }
  function submitChecklistOrder(ids: string[]) {
    const clean = realIds(ids, task.checklists ?? []);
    if (clean.length === 0) return;
    orderIds = clean.join(",");
    // Next tick, so the hidden input carries the fresh value (the projects board's recipe).
    setTimeout(() => checklistOrderForm?.requestSubmit(), 0);
  }
  function submitItemOrder(checklistId: string, ids: string[]) {
    const stored = (task.checklists ?? []).find((cl) => cl.id === checklistId);
    const clean = realIds(ids, stored?.items ?? []);
    if (clean.length === 0) return;
    orderChecklistId = checklistId;
    orderIds = clean.join(",");
    setTimeout(() => itemOrderForm?.requestSubmit(), 0);
  }

  /** Swap `id` with its neighbour `delta` away, or do nothing at the ends. */
  function swapped(ids: string[], id: string, delta: number): string[] | null {
    const index = ids.indexOf(id);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= ids.length) return null;
    const next = [...ids];
    [next[index], next[target]] = [next[target], next[index]];
    return next;
  }
  function moveChecklist(id: string, delta: number) {
    const next = swapped(
      dndChecklists.map((cl) => cl.id),
      id,
      delta,
    );
    if (next) submitChecklistOrder(next);
  }
  function moveItem(checklistId: string, id: string, delta: number) {
    const next = swapped(
      (dndItems[checklistId] ?? []).map((i) => i.id),
      id,
      delta,
    );
    if (next) submitItemOrder(checklistId, next);
  }

  function considerChecklists(e: CustomEvent<{ items: ChecklistRow[] }>) {
    dndChecklists = e.detail.items;
  }
  function finalizeChecklists(e: CustomEvent<{ items: ChecklistRow[] }>) {
    dndChecklists = e.detail.items;
    dragChecklists = false;
    submitChecklistOrder(dndChecklists.map((cl) => cl.id));
  }
  function considerItems(checklistId: string, e: CustomEvent<{ items: ItemRow[] }>) {
    dndItems = { ...dndItems, [checklistId]: e.detail.items };
  }
  function finalizeItems(checklistId: string, e: CustomEvent<{ items: ItemRow[] }>) {
    dndItems = { ...dndItems, [checklistId]: e.detail.items };
    dragItemsIn = null;
    submitItemOrder(
      checklistId,
      e.detail.items.map((i) => i.id),
    );
  }
  // Bumped after a comment is posted to remount (and so clear) the markdown editor.
  let newCommentKey = $state(0);

  // --- comment threads (#312) ------------------------------------------------------------- //
  // The API hands back one flat, chronological list carrying `parent_id`; the nesting is a
  // display concern, so it is built here. Threads are one level deep by construction (the
  // service re-roots a reply-to-a-reply), which is what lets this be a group-by rather than a
  // recursive component — and what keeps a conversation readable at one indent on a phone.
  type TaskComment = NonNullable<(typeof task)["comments"]>[number];
  const threads = $derived.by(() => {
    const list = task.comments ?? [];
    const out: { root: TaskComment; replies: TaskComment[] }[] = [];
    // uuid keys, so a plain record indexes them safely — and stays outside Svelte's reactivity
    // rules, which a Map/Set built inside a $derived would trip for no benefit.
    const at: Record<string, number> = Object.create(null);
    const present: Record<string, true> = Object.create(null);
    for (const c of list) present[c.id] = true;
    for (const c of list) {
      // A reply whose parent fell outside the response cap opens its own thread rather than
      // vanishing: the read orders by thread so this is rare, but "nothing is shown" is never
      // the better answer to "the conversation is longer than 200 messages".
      const parent = c.parent_id && present[c.parent_id] ? c.parent_id : null;
      const idx = parent === null ? undefined : at[parent];
      if (idx === undefined) {
        at[c.id] = out.length;
        out.push({ root: c, replies: [] });
      } else {
        out[idx].replies.push(c);
      }
    }
    return out;
  });

  /** Which thread's reply composer is open, by root id — one at a time, like the edit form. */
  let replyingTo = $state<string | null>(null);
  /** Seeded body for that composer: answering a *reply* addresses its author by name, so the
   *  thread still says who is being answered once several people are in it. */
  let replySeed = $state("");
  /** Remounts the composer, so opening it on another thread never inherits a stale draft. */
  let replyKey = $state(0);

  function openReply(rootId: string, answering: TaskComment) {
    // The mention marker is the editor's own syntax (`core/richtext/editor.ts`), so the seed
    // round-trips into a real mention chip rather than literal text. Only for someone with a
    // live account — a departed author has no id to mention.
    replySeed =
      answering.id === rootId || !answering.author_user_id || !answering.author_name
        ? ""
        : `@[${answering.author_name}](mention:${answering.author_user_id}) `;
    replyingTo = rootId;
    replyKey += 1;
  }

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
  // Duplicating a checklist asks for the copy's name up front, the way duplicating a role does:
  // two lists called "Website check" side by side is exactly what the user is trying to avoid.
  let duplicateOpen = $state(false);
  let duplicateChecklistId = $state("");
  let duplicateTitle = $state("");
  function askDuplicate(id: string, title: string) {
    duplicateChecklistId = id;
    duplicateTitle = title;
    duplicateOpen = true;
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
    if (
      a.action === "checklist_renamed" ||
      a.action === "checklist_item_renamed" ||
      a.action === "checklist_duplicated"
    ) {
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
    // Deleting a thread opener took its answers with it (#312) — the trail says how many, or a
    // five-message conversation disappears behind a line describing one comment.
    if (a.action === "comment_deleted" && Number(a.payload.replies ?? 0) > 0) {
      const count = Number(a.payload.replies);
      const excerpt = String(a.payload.excerpt ?? "");
      return count === 1
        ? t("tasks.activity.comment_deleted_thread_one", { excerpt })
        : t("tasks.activity.comment_deleted_thread_other", { count, excerpt });
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
          <!-- Use mode only: while editing, the checkbox below is the live answer and a header
               marker still showing the *stored* one would contradict it mid-edit. -->
          <ClientVisibilityIcon
            visible={task.visible_to_client}
            companyId={task.company_id}
            projectId={task.project_id}
            size={16}
          />
        {/if}

        <!-- Each item asks the key its own call declares, and the menu disappears when nothing
             survives (#253). It used to hang off `!isPortal` alone — which is right about a
             portal contact (they work the task, never its definition) and wrong about everyone
             else: a member holding `tasks.task.write:own` was offered Bewerken on a colleague's
             task, and *every* staff viewer was offered Verwijderen, an admin-only permission. -->
        {#if canEditTask || canDeleteTask}
          <ActionsMenu
            items={[
              ...(canEditTask
                ? [
                    {
                      label: editMode ? t("tasks.detail.done_editing") : t("common.edit"),
                      icon: Pencil,
                      onclick: () => {
                        // Re-arm the relation picks so a stale pick never overrides the stored
                        // relation on a later edit session.
                        fCompany = task.company_id ?? "";
                        fProject = task.project_id ?? "";
                        editMode = !editMode;
                      },
                    },
                  ]
                : []),
              ...(canDeleteTask
                ? [
                    {
                      label: t("tasks.detail.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => (confirmDelete = true),
                    },
                  ]
                : []),
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
            scope={candidateScope}
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

        <!-- Two hidden forms carry a whole order to the API — one for the checklists, one for the
             items of whichever list was dragged. Filled by `submit*Order`, submitted next tick. -->
        {#if editMode}
          <form
            method="POST"
            action="?/reorderChecklists"
            use:enhance
            bind:this={checklistOrderForm}
            class="hidden"
          >
            <input type="hidden" name="ids" value={orderIds} />
          </form>
          <form
            method="POST"
            action="?/reorderItems"
            use:enhance
            bind:this={itemOrderForm}
            class="hidden"
          >
            <input type="hidden" name="checklist_id" value={orderChecklistId} />
            <input type="hidden" name="ids" value={orderIds} />
          </form>
        {/if}

        <div
          use:dndzone={{
            items: dndChecklists,
            flipDurationMs: 150,
            dropTargetStyle: {},
            type: "task-checklists",
            dragDisabled: !editMode || !dragChecklists,
          }}
          onconsider={considerChecklists}
          onfinalize={finalizeChecklists}
        >
          {#each dndChecklists as checklist, checklistIndex (checklist.id)}
            {@const items = dndItems[checklist.id] ?? []}
            {@const total = items.length}
            {@const doneCount = items.filter((i) => i.done).length}
            <div class="mb-4 bg-surface-raised">
              <div class="mb-1 flex items-center justify-between gap-2">
                <div class="flex min-w-0 items-center gap-1">
                  {#if editMode}
                    <button
                      type="button"
                      class="-ml-1 shrink-0 cursor-grab touch-none text-text-muted active:cursor-grabbing"
                      aria-label={t("tasks.checklist.drag", { title: checklist.title })}
                      onpointerdown={() => (dragChecklists = true)}
                    >
                      <GripVertical size={14} />
                    </button>
                  {/if}
                  <h4 class="truncate text-sm font-semibold text-text">{checklist.title}</h4>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs tabular-nums text-text-muted"
                    >{t("tasks.checklist.progress", { done: doneCount, total })}</span
                  >
                  {#if editMode && items.length > 0 && canSaveChecklistTemplate}
                    <form method="POST" action="?/saveChecklistTemplate" use:enhance>
                      <input type="hidden" name="title" value={checklist.title} />
                      <!-- Item titles *and* descriptions, so the saved template carries both (issue #66). -->
                      <input
                        type="hidden"
                        name="items"
                        value={JSON.stringify(
                          items.map((i) => ({
                            title: i.title,
                            description: i.description ?? null,
                          })),
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
                          label: t("tasks.checklist.move_up"),
                          icon: ArrowUp,
                          disabled: checklistIndex === 0,
                          onclick: () => moveChecklist(checklist.id, -1),
                        },
                        {
                          label: t("tasks.checklist.move_down"),
                          icon: ArrowDown,
                          disabled: checklistIndex === dndChecklists.length - 1,
                          onclick: () => moveChecklist(checklist.id, 1),
                        },
                        {
                          label: t("common.edit"),
                          icon: Pencil,
                          onclick: () =>
                            (editingChecklistId =
                              editingChecklistId === checklist.id ? null : checklist.id),
                        },
                        {
                          label: t("tasks.checklist.duplicate"),
                          icon: Copy,
                          onclick: () => askDuplicate(checklist.id, checklist.title),
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
                  use:enhance={busy.wrap("editChecklist", () => ({ update }) => {
                    editingChecklistId = null;
                    void update({ reset: false });
                  })}
                  class="mb-2 space-y-2"
                >
                  <input type="hidden" name="checklist_id" value={checklist.id} />
                  <input name="title" value={checklist.title} required class={inputClass} />
                  <RichTextEditor
                    name="description"
                    rows={2}
                    value={checklist.description ?? ""}
                    placeholder={t("tasks.checklist.description_placeholder")}
                    scope={candidateScope}
                  />
                  <div class="flex gap-2">
                    <Button size="xs" loading={busy.is("editChecklist")}>{t("common.save")}</Button>
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
              <!-- Items reorder within their own list: a distinct `type` per checklist, so a drag
                 cannot drop a to-do into the list next door (that is a move, not a reorder, and
                 no endpoint here promises it). -->
              <ul
                class="space-y-1"
                use:dndzone={{
                  items,
                  flipDurationMs: 150,
                  dropTargetStyle: {},
                  type: `checklist-items-${checklist.id}`,
                  dragDisabled: !editMode || dragItemsIn !== checklist.id,
                }}
                onconsider={(e) => considerItems(checklist.id, e)}
                onfinalize={(e) => finalizeItems(checklist.id, e)}
              >
                {#each items as item, itemIndex (item.id)}
                  <li class="group bg-surface-raised">
                    <div class="flex items-center gap-2">
                      {#if editMode}
                        <button
                          type="button"
                          class="-mr-1 shrink-0 cursor-grab touch-none text-text-muted active:cursor-grabbing"
                          aria-label={t("tasks.checklist.drag_item", { title: item.title })}
                          onpointerdown={() => (dragItemsIn = checklist.id)}
                        >
                          <GripVertical size={13} />
                        </button>
                      {/if}
                      {#if canEditTask}
                        <form
                          method="POST"
                          action="?/toggleItem"
                          use:enhance={({ formData }) => {
                            // Ticking is the most-repeated gesture on this page, and it used to
                            // cost a whole page reload: `update()` invalidates every load above
                            // it, so one checkbox re-ran the two layouts and this page —
                            // sixteen API calls, one of them the eight-round-trip task detail —
                            // and the box did not change colour until all of it came back.
                            //
                            // So flip it here and let the PATCH catch up. `item` is the object
                            // the drag arrays hold, so the checkbox, the progress bar, the
                            // "3/7" and `openItemCount` all move with this one write. Nothing
                            // is invalidated: the only thing the server changed that this page
                            // also draws is the activity line, which the next load picks up
                            // (the NotificationBell's fire-and-forget precedent).
                            //
                            // `next` comes from the serialised body, not from `item.done`, so
                            // what we show can never disagree with what we sent.
                            const next = formData.get("done") === "true";
                            item.done = next;
                            // Read *after* the flip — this is the tick that emptied the list.
                            const completesLast =
                              next &&
                              openItemCount === 0 &&
                              !isDone &&
                              !isPortal &&
                              finishStatus !== null;
                            return async ({ result }) => {
                              // Refused (a lost race, a permission withdrawn mid-session): put
                              // the box back rather than leave the screen claiming a change the
                              // server never made. `applyAction` surfaces the message and — the
                              // reason it is used instead of `update()` — invalidates nothing.
                              if (result.type !== "success") item.done = !next;
                              await applyAction(result);
                              if (result.type === "success" && completesLast) {
                                showFinishPrompt = true;
                              }
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
                      {:else}
                        <!-- Read-only viewer (portal client, #244): item state shows, ticking does not. -->
                        <span
                          class="flex h-4 w-4 items-center justify-center rounded border text-[10px]
                        {item.done
                            ? 'border-brand bg-brand text-white'
                            : 'border-border text-transparent'}"
                          aria-label={t("tasks.toggle_done")}>✓</span
                        >
                      {/if}
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
                              label: t("tasks.checklist.move_up"),
                              icon: ArrowUp,
                              disabled: itemIndex === 0,
                              onclick: () => moveItem(checklist.id, item.id, -1),
                            },
                            {
                              label: t("tasks.checklist.move_down"),
                              icon: ArrowDown,
                              disabled: itemIndex === items.length - 1,
                              onclick: () => moveItem(checklist.id, item.id, 1),
                            },
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
                        use:enhance={busy.wrap("editItem", () => ({ update }) => {
                          editingItemId = null;
                          void update({ reset: false });
                        })}
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
                          scope={candidateScope}
                        />
                        <div class="flex gap-2">
                          <Button size="xs" loading={busy.is("editItem")}>{t("common.save")}</Button
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
              {#if canEditTask}
                <!-- Quick-add is a task write (POST item); hidden from a read-only portal client (#244). -->
                <form
                  method="POST"
                  action="?/addItem"
                  use:enhance={busy.wrap(`addItem:${checklist.id}`)}
                  class="mt-2 flex gap-2"
                >
                  <input type="hidden" name="checklist_id" value={checklist.id} />
                  <input
                    name="title"
                    placeholder={t("tasks.checklist.item_placeholder")}
                    required
                    class="min-w-0 flex-1 rounded-lg border border-border px-2 py-1 text-sm outline-none focus:border-brand"
                  />
                  <Button variant="secondary" size="xs" loading={busy.is(`addItem:${checklist.id}`)}
                    >＋</Button
                  >
                </form>
              {/if}
            </div>
          {/each}
        </div>

        {#if editMode}
          <form
            method="POST"
            action="?/addChecklist"
            use:enhance={busy.wrap("addChecklist")}
            class="flex gap-2"
          >
            <!-- `min-w-0`: a flex `<input>` keeps its browser-default width (~228px here) as its
               min-content floor, so `flex-1` alone cannot shrink it and the row pushed the whole
               card past a phone's width (issue #36). -->
            <input
              name="title"
              placeholder={t("tasks.checklist.add")}
              required
              class="min-w-0 flex-1 rounded-lg border border-dashed border-border px-3 py-1.5 text-sm outline-none focus:border-brand"
            />
            <Button variant="secondary" size="sm" loading={busy.is("addChecklist")}>
              {t("common.create")}
            </Button>
          </form>
          {#if data.checklistTemplates.length > 0}
            <form
              method="POST"
              action="?/addChecklist"
              use:enhance={busy.clear("addChecklistTpl")}
              class="mt-2 flex gap-2"
            >
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
              <Button variant="secondary" size="sm" loading={busy.is("addChecklistTpl")}>
                {t("tasks.checklist.from_template")}
              </Button>
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
            use:enhance={busy.wrap(
              "addLink",
              () =>
                ({ update }) =>
                  void update({ reset: true }),
            )}
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
            <Button variant="secondary" size="sm" loading={busy.is("addLink")}>
              {t("common.create")}
            </Button>
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
              readonly={!editMode || !canWriteFile}
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

      <!-- POST /tasks/{id}/comments declares `tasks.comment.write`, and the editor was drawn for
           everyone who could read the task: a role without it typed a comment and lost it to a
           403 on send. The scope is not consulted — a comment you post is your own, so the API
           refines nothing here (`TaskService.add_comment`). -->
      {#if canComment}
        <form
          method="POST"
          action="?/addComment"
          use:enhance={busy.wrap("addComment", () => ({ update, result }) => {
            // Reset the editor by remounting it; its internal state survives a plain form reset.
            if (result.type === "success") newCommentKey += 1;
            void update({ reset: true });
          })}
          class="mb-4"
        >
          {#key newCommentKey}
            <RichTextEditor
              name="body"
              rows={2}
              required
              placeholder={t("tasks.comments.placeholder")}
              scope={candidateScope}
            />
          {/key}
          <div class="mt-2 flex justify-end">
            <Button size="sm" loading={busy.is("addComment")}>{t("tasks.comments.send")}</Button>
          </div>
        </form>
      {/if}

      <!-- One bubble, rendered for a thread opener and for an answer alike (#312): the two differ
           in where they sit and how loud they are, never in what they can do. Duplicating the
           markup would have been two places to keep the ⋯ menu, the edit form and the
           impersonation badge in step. -->
      {#snippet commentBubble(
        comment: TaskComment,
        rootId: string,
        replyCount: number,
        isReply: boolean,
      )}
        <!-- Being the author is half of it: `update_comment` refuses a non-author outright
             and still requires the key from the author. Deleting your own needs the same
             key; deleting someone else's needs it at `:any`. -->
        {@const canEditComment = comment.author_user_id === userId && canComment}
        {@const canDeleteComment = canEditComment || canDeleteAnyComment}
        <div
          id="comment-{comment.id}"
          class="rounded-lg border border-border p-3 {isReply ? 'bg-surface/30' : 'bg-surface/50'}"
        >
          <div class="mb-1 flex items-center justify-between gap-2">
            <span class="flex items-center gap-1.5 text-xs font-semibold text-text">
              {authorLabel(comment)}
              <!-- Written through this account by someone else (#296): the agency's own words
                   would otherwise sit under the client's name with nothing to say so. -->
              {#if comment.impersonator_name}
                <span
                  class="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-300"
                  title={t("activity.impersonated_title", {
                    actor: comment.impersonator_name,
                  })}
                >
                  {t("activity.via_impersonator", { actor: comment.impersonator_name })}
                </span>
              {/if}
            </span>
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
                      // Deleting a thread opener takes its answers with it (ON DELETE CASCADE),
                      // so the confirm counts them: a dialog that says "this comment" while five
                      // messages disappear is the one thing an undo-less delete may not do.
                      onclick: () =>
                        askDelete(
                          "?/deleteComment",
                          { comment_id: comment.id },
                          replyCount === 0
                            ? t("tasks.comments.delete_confirm")
                            : replyCount === 1
                              ? t("tasks.comments.delete_thread_confirm_one")
                              : t("tasks.comments.delete_thread_confirm_other", {
                                  count: replyCount,
                                }),
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
              use:enhance={busy.wrap("editComment", () => ({ update }) => {
                editingCommentId = null;
                void update({ reset: false });
              })}
            >
              <input type="hidden" name="comment_id" value={comment.id} />
              <RichTextEditor
                name="body"
                rows={2}
                required
                value={comment.body}
                scope={candidateScope}
              />
              <div class="mt-1 flex gap-2">
                <Button size="xs" loading={busy.is("editComment")}>{t("common.save")}</Button>
                <button
                  type="button"
                  class="rounded-lg border border-border px-2 py-1 text-xs"
                  onclick={() => (editingCommentId = null)}>{t("common.cancel")}</button
                >
              </div>
            </form>
          {:else}
            <Markdown value={comment.body} />
            <!-- Answering is not "editing the definition", so it stays inline rather than hiding
                 in the ⋯ menu (docs/UX.md). It gates on the same permission the POST declares —
                 a client portal login holds it, and its own task comments are its whole write
                 surface. -->
            {#if canComment}
              <button
                type="button"
                class="mt-1.5 inline-flex items-center gap-1 rounded text-[11px] font-medium text-text-muted hover:text-text"
                onclick={() => openReply(rootId, comment)}
              >
                <Reply class="size-3" aria-hidden="true" />
                {t("tasks.comments.reply")}
              </button>
            {/if}
          {/if}
        </div>
      {/snippet}

      {#if threads.length === 0}
        <p class="text-sm text-text-muted">{t("tasks.comments.empty")}</p>
      {:else}
        <ul class="space-y-3">
          {#each threads as thread (thread.root.id)}
            <li>
              {@render commentBubble(thread.root, thread.root.id, thread.replies.length, false)}

              <!-- Answers hang off their opener under one rule, at one indent. A second level
                   would indent itself off a phone; the API re-roots instead of nesting deeper. -->
              {#if thread.replies.length > 0}
                <ul class="mt-2 space-y-2 border-l-2 border-border pl-3 sm:pl-4">
                  {#each thread.replies as reply (reply.id)}
                    <li>{@render commentBubble(reply, thread.root.id, 0, true)}</li>
                  {/each}
                </ul>
              {/if}

              {#if replyingTo === thread.root.id}
                <form
                  method="POST"
                  action="?/addComment"
                  use:enhance={busy.wrap("addComment", () => ({ update, result }) => {
                    // Close on success, keep the draft on failure — the words are not the
                    // server's to throw away (docs/UX.md, the reset rule).
                    if (result.type === "success") replyingTo = null;
                    void update({ reset: result.type === "success" });
                  })}
                  class="mt-2 border-l-2 border-brand/40 pl-3 sm:pl-4"
                >
                  <input type="hidden" name="parent_id" value={thread.root.id} />
                  <p class="mb-1 text-[11px] text-text-muted">
                    {t("tasks.comments.reply_to", { name: authorLabel(thread.root) })}
                  </p>
                  {#key replyKey}
                    <RichTextEditor
                      name="body"
                      rows={2}
                      required
                      value={replySeed}
                      placeholder={t("tasks.comments.reply_placeholder")}
                      scope={candidateScope}
                    />
                  {/key}
                  <div class="mt-2 flex gap-2">
                    <Button size="xs" loading={busy.is("addComment")}
                      >{t("tasks.comments.send")}</Button
                    >
                    <button
                      type="button"
                      class="rounded-lg border border-border px-2 py-1 text-xs"
                      onclick={() => (replyingTo = null)}>{t("common.cancel")}</button
                    >
                  </div>
                </form>
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
                  <!-- Someone was signed in as them (#296) — a client's comment written by the
                       agency reads as the client's until this says otherwise. -->
                  {#if activity.impersonator_name}
                    <span
                      class="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-300"
                      title={t("activity.impersonated_title", {
                        actor: activity.impersonator_name,
                      })}
                    >
                      {t("activity.via_impersonator", { actor: activity.impersonator_name })}
                    </span>
                  {/if}
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
        <!-- Status is core workflow → editable outside edit mode, but it is still a task write
             (PATCH /tasks/{id}), so it asks the same per-row question every other write control
             on this page does. `!isPortal` gave a `:own` member a live status dropdown on a
             colleague's task; everyone else reads the value. -->
        <div>
          <label for="status" class="mb-1 block text-xs font-medium text-text-muted"
            >{t("tasks.field.status")}</label
          >
          {#if !canEditTask}
            <p id="status" class="text-sm text-text">
              {statuses.find((s) => s.key === task.status)?.name ?? task.status}
            </p>
          {:else}
            <form method="POST" action="?/update" use:enhance={busy.keep("status")}>
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
          <!-- The client comes first because it narrows both fields under it: the contact half of
               the assignee picker, and the project list. Picking it last meant choosing from an
               unnarrowed set and then watching it shrink. -->
          <div>
            <label for="company" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.company")}</label
            >
            <Combobox
              items={companyItems}
              name="company_id"
              value={fCompany}
              id="company"
              formId="task-edit"
              onselect={onCompanyPicked}
              oncreate={(name) => {
                qcCompanyName = name;
                qcCompanyOpen = true;
              }}
            />
          </div>
          <div>
            <label for="assignee-entity" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.assignee")}</label
            >
            <!-- Employee, or — when the task has a client (#273) — one of that client's contacts.
                 The contact list follows the live company pick above (fCompany). -->
            <TaskAssigneePicker
              formId="task-edit"
              employees={data.members}
              contacts={assigneeContacts}
              contactsEnabled={!!fCompany}
              userValue={task.assignee_user_id ?? ""}
              contactValue={task.assignee_contact_id ?? ""}
            />
          </div>
          <div>
            <label for="project" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.project")}</label
            >
            <Combobox
              items={projectItems}
              name="project_id"
              value={fProject}
              id="project"
              formId="task-edit"
              onselect={onProjectPicked}
              oncreate={(name) => {
                qcProjectName = name;
                qcProjectOpen = true;
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
            <!-- Same order as the edit form above: client, assignee, project. -->
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
              <dt class="text-xs font-medium text-text-muted">{t("tasks.field.assignee")}</dt>
              <dd class="text-text">
                <!-- A contact assignee (#273) reads with its kind, so it isn't mistaken for an
                     employee; its name resolves from the client's contacts, loaded client-side. -->
                {#if task.assignee_contact_id}
                  {contactName(task.assignee_contact_id) ?? t("party.contact")}
                  <span class="text-xs text-text-muted">({t("party.contact")})</span>
                {:else}
                  {memberName(task.assignee_user_id) ?? "—"}
                {/if}
              </dd>
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
            {#if !isPortal}
              <!-- The card's own statement of what the header marker draws: an icon carries its
                   meaning in a `title=`, which a phone has no way to show. Staff-only — a client
                   reading their own task learns nothing from "yes, you can see this". -->
              <div class="flex items-center justify-between gap-2">
                <dt class="text-xs font-medium text-text-muted">
                  {t("tasks.field.visible_to_client")}
                </dt>
                <dd class="flex items-center gap-1.5 text-text">
                  <ClientVisibilityIcon
                    visible={task.visible_to_client}
                    companyId={task.company_id}
                    projectId={task.project_id}
                    size={13}
                  />
                  {task.visible_to_client ? t("common.yes") : t("common.no")}
                </dd>
              </div>
            {/if}
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
            use:enhance={busy.wrap("setLabels", () => ({ update }) => {
              showLabelPicker = false;
              void update({ reset: false });
            })}
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
            <Button size="sm" loading={busy.is("setLabels")} class="mt-2 w-full"
              >{t("common.apply")}</Button
            >
          </form>

          <!-- Ticking labels onto this task is `tasks.task.write` (above); *minting* one adds a
               row to the org's vocabulary and is `tasks.label.write`, which nobody but an admin
               holds by default. Same split as "Als sjabloon opslaan" further up the page. -->
          {#if canWriteLabels}
            <form
              method="POST"
              action="?/createLabel"
              use:enhance={busy.wrap("createLabel", () => ({ update }) => {
                showLabelPicker = false;
                void update();
              })}
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
              <Button
                variant="secondary"
                size="sm"
                loading={busy.is("createLabel")}
                class="mt-2 w-full"
              >
                {t("tasks.labels.create")}
              </Button>
            </form>
          {/if}
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
        use:enhance={busy.wrap("update", () => ({ update }) => {
          editMode = false;
          dueReason = "";
          void update();
        })}
      >
        <input type="hidden" name="due_change_reason" value={dueReason} />
        <Button loading={busy.is("update")} class="w-full">
          {t("common.save")}
        </Button>
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

<!-- Duplicate a checklist: one field, because the only thing the copy needs from the user is its
     name. What travels (items and their descriptions) and what does not (the ticks) is stated,
     not left to be discovered after the fact. -->
<Modal bind:open={duplicateOpen} title={t("tasks.checklist.duplicate")}>
  <form
    method="POST"
    action="?/duplicateChecklist"
    use:enhance={busy.wrap("duplicateChecklist", () => async ({ result, update }) => {
      if (result.type === "success") duplicateOpen = false;
      // The copy is a new record, not this form's subject, so the field may empty — and
      // reopening the dialog fills it from the checklist that was picked anyway.
      await update({ reset: true });
    })}
    class="space-y-3"
  >
    <input type="hidden" name="checklist_id" value={duplicateChecklistId} />
    <div>
      <label for="checklist-duplicate-title" class="mb-1 block text-sm font-medium text-text"
        >{t("tasks.checklist.duplicate_title")}</label
      >
      <input
        id="checklist-duplicate-title"
        name="title"
        bind:value={duplicateTitle}
        required
        class={inputClass}
      />
    </div>
    <p class="text-xs text-text-muted">{t("tasks.checklist.duplicate_hint")}</p>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (duplicateOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("duplicateChecklist")}>{t("tasks.checklist.duplicate")}</Button>
    </div>
  </form>
</Modal>

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
        use:enhance={busy.wrap("finish", () => ({ update }) => {
          showFinishPrompt = false;
          return update();
        })}
      >
        <input type="hidden" name="status" value={finishStatus?.key ?? ""} />
        <Button loading={busy.is("finish")}>
          {t("tasks.finish_prompt.confirm")}
        </Button>
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
      use:enhance={busy.wrap("createProject", () => ({ result, update }) => {
        if (result.type === "success") qcProjectOpen = false;
        void update({ reset: false });
      })}
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
        <!-- Required: a project belongs to a client. The task's own client is the default,
             and a task that has none makes this the one field to fill in. -->
        <Combobox
          items={companyItems}
          name="company_id"
          value={fCompany}
          id="qc-task-project-company"
          allowEmpty={false}
          placeholder={t("projects.field.company")}
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
        <Button loading={busy.is("createProject")}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>
