<script lang="ts">
  import {
    ArrowDown,
    ArrowUp,
    Copy,
    GripVertical,
    Link as LinkIcon,
    Pencil,
    Trash2,
  } from "@lucide/svelte";
  import { tick } from "svelte";
  import { dndzone } from "svelte-dnd-action";

  import { applyAction, enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { clearEditIntent, editIntent } from "$lib/core/edit-intent";
  import { fmtDateTime, fmtDayMonth, fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { originOf, withOrigin } from "$lib/core/origin";
  import { pageTitle } from "$lib/core/title";
  import { orgToday } from "$lib/core/today";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Assignees from "$lib/core/ui/Assignees.svelte";
  import BudgetBar from "$lib/core/ui/BudgetBar.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import DurationInput from "$lib/core/ui/DurationInput.svelte";
  import InlineField from "$lib/core/ui/InlineField.svelte";
  import InlineText from "$lib/core/ui/InlineText.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import { taskBurn } from "$lib/modules/tasks/budget";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { dueBucket, dueNote } from "$lib/modules/tasks/due";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskAIStatus from "$lib/modules/tasks/TaskAIStatus.svelte";
  import RecurrenceEditor from "$lib/modules/tasks/RecurrenceEditor.svelte";
  import { planSummary, recurrenceSentence, type Recurrence } from "$lib/modules/tasks/recurrence";
  import { localDayTime } from "$lib/modules/tasks/schedule";
  import TaskAssigneePicker from "$lib/modules/tasks/TaskAssigneePicker.svelte";
  import TaskComments from "$lib/modules/tasks/TaskComments.svelte";
  import { readCommentSort, type CommentSort } from "$lib/modules/tasks/comment-prefs";
  import TaskSchedulePanel from "$lib/modules/tasks/TaskSchedulePanel.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  import { entityPanelSpec } from "$lib/core/registry";
  import Card from "$lib/core/ui/Card.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import { PANEL_HEADING } from "$lib/core/ui/headings";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  let { data, form } = $props();

  const task = $derived(data.task);

  // Panels contributed by enabled modules (CLAUDE.md §6) — contactmomenten, Drive, and
  // whatever ships later, composed exactly like the project page does.
  const enabledModules = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpec = (key: string) => entityPanelSpec(enabledModules, "task", key);
  const panelLookups = $derived({
    members: data.members,
    companies: data.companies,
    projects: data.projects,
    // The current task — always, and carrying **both** parents. A panel reaching for this task's
    // client (the Drive panel roots the browser at the project/client folder rather than the
    // shared-drive root, #150) used to get it only by walking through a project, so a task
    // attached straight to a client resolved nothing and opened at the root (#363).
    tasks: [
      { id: task.id, title: task.title, project_id: task.project_id, company_id: task.company_id },
    ],
  });

  // The activity log grows without bound on a busy task (issue #86): show the most recent few and
  // expand the rest in place. Rows are newest-first, so the head is the newest. The third
  // verbatim copy of this collapse until #407; `PanelRows` owns it now.
  const ACTIVITY_COLLAPSED = 3;
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
  const userId = $derived(page.data.user?.id ?? "");
  // A portal login (#193) works the task, not the office around it: uploads, the activity
  // trail, time budgets and module panels (interactions, Drive) stay staff-only. The API
  // enforces the same (portal activity feed is empty; time/interactions are permission-gated);
  // this keeps the page honest about it.
  const isPortal = $derived(page.data.user?.isPortal ?? false);

  /**
   * The page's sections and the panels enabled modules contribute are **one** ordered list
   * (#393). Until now there were two orderings that could not be interleaved: the source
   * order of the hand-written sections, and the `position` each panel declares. Drive (55)
   * was therefore stuck below Reacties however it was asked for, because every panel
   * rendered after every section. Both live on one scale now, so moving a section is one
   * number here and no module is edited (CLAUDE.md §6, a panel still decides its own place).
   *
   * The order the team asked for: what has to happen (Omschrijving, Checklists) before when
   * it has to happen (Planning), then the two file surfaces beside each other (Links &
   * bijlagen 50, Drive 55), the discussion, contactmomenten (60), and the trail last.
   */
  const SECTION_POSITIONS = {
    properties: 10,
    description: 20,
    checklists: 30,
    planning: 40,
    links: 50,
    comments: 58,
    activity: 90,
  } as const;
  type SectionKey = keyof typeof SECTION_POSITIONS;

  const orderedSections = $derived(
    [
      ...(Object.entries(SECTION_POSITIONS) as [SectionKey, number][]).map(([key, position]) => ({
        kind: "page" as const,
        key,
        position,
      })),
      // A portal login gets no module panels at all (see `isPortal` above).
      ...(isPortal ? [] : data.panels).map((panel) => ({
        kind: "panel" as const,
        key: panel.key,
        position: panel.position,
        panel,
      })),
    ].sort((a, b) => a.position - b.position),
  );
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
  // Planning is a third key again (#188). The recurrence editor's "plan ook in" mirrors these two
  // rather than the task write it rides in on: putting a block on a colleague's calendar is a
  // different capability, and the API asks the same pair when the rule is stored (#335).
  const canSchedule = $derived(can(page.data.user, "tasks.schedule.write"));
  const canScheduleAny = $derived(can(page.data.user, "tasks.schedule.write", "any"));

  // --- Planning: one home for "when" (#335) ----------------------------------------------- //
  const recurrence = $derived((task.recurrence ?? null) as Recurrence | null);
  /** The hour someone last planned by hand — the auto-plan's best guess at "and at what time?". */
  const lastBlockStart = $derived.by(() => {
    const latest = [...(data.schedules ?? [])].sort((a, b) =>
      b.starts_at.localeCompare(a.starts_at),
    )[0];
    return latest ? localDayTime(latest.starts_at).time : null;
  });

  // The org's configured status vocabulary (issue #62), from the /tasks layout load.
  const statuses = $derived(data.statuses);
  const statusName = (key: string) => statuses.find((s) => s.key === key)?.name ?? key;
  const isDone = $derived(statuses.find((s) => s.key === task.status)?.is_terminal ?? false);
  // The sidebar select's live value, Svelte-owned rather than read off the DOM: the finish
  // prompt puts the pick back while it asks (#314), and the record is the authority again the
  // moment a write lands. A *writable* derived is exactly that pair of rules in one line — it
  // follows the record, and an assignment holds only until the record next moves.
  let statusValue = $derived(task.status);

  // Ticking the *last* open to-do offers to finish the task (the to-dos and the status should
  // not drift apart silently). If finishing is gated on a closing contact moment (#157 — the
  // task's own flag, or the terminal status's), the prompt says so instead of offering a move
  // that the API would refuse.
  let showFinishPrompt = $state(false);
  // How it opened, because the two paths are asking different questions: "every to-do is
  // ticked, shall we finish?" versus "you picked a finished status, shall we?".
  let finishReason = $state<"checklist" | "status">("checklist");
  // `openItemCount` counts the rows the *screen* holds (`dndItems`, declared below) rather than
  // the ones the load returned: a tick is optimistic now, so the record is a round trip behind
  // the checkbox and counting it would arm the finish prompt one tick late.
  const finishStatus = $derived(statuses.find((s) => s.is_terminal) ?? null);
  // Which finished status the confirm will post. The checklist path has no opinion and takes
  // the first one; the status select carries the one the user actually picked, or the prompt
  // would quietly move the task somewhere else.
  let finishTargetKey = $state("");
  const finishTarget = $derived(
    statuses.find((s) => s.key === finishTargetKey && s.is_terminal) ?? finishStatus,
  );
  const needsClosingMoment = (target: { requires_interaction?: boolean } | null | undefined) =>
    (task.requires_interaction || (target?.requires_interaction ?? false)) &&
    !task.closing_interaction_id;
  const finishNeedsMoment = $derived(needsClosingMoment(finishTarget));

  // --- "Ook de uren registreren" (#314) ------------------------------------------------- //
  // Finishing a task and recording the hours it took are one act, so they are one form and one
  // request. Nothing here costs the page load a thing: every source the suggestion draws on is
  // already on `data` (the task's own budget, its planned blocks), which is the whole reason
  // this is a small dedicated fieldset rather than a mounted `EntryForm` — that one needs the
  // companies/projects/tasks/members payload the /time layout fetches, and pulling it onto
  // every task page to serve one occasional dialog is a cost nobody would get back.
  //
  // The offer is skipped entirely where it could not lead anywhere. A dialog that appears on
  // every single tick and can only be dismissed is how this feature would die.
  // Time budget. This used to hand-roll the 75/100 ladder in its own `bg-green-500`/amber/red
  // and clamp its own bar — a fourth copy of a scale that has lived in `core/burn.ts` for
  // months, and the one copy that had already drifted (#313). Both the numbers and the block
  // are shared now; `null` means the API withheld the burn (a client-portal login holds
  // `tasks.task.read` and never `time.entry.read`), so nothing is drawn at all.
  const burn = $derived(taskBurn(task));

  const entitledModules = $derived(page.data.theme?.entitledModules ?? []);
  const canLogHours = $derived(
    !isPortal &&
      can(page.data.user, "time.entry.write") &&
      enabledModules.includes("time") &&
      // A lapsed licence makes `time` read-only (#137): hidden rather than locked, because a
      // padlock inside a confirm dialog is noise on a screen that is not about buying anything.
      entitledModules.includes("time"),
  );
  const offerLogTime = $derived(
    canLogHours &&
      // Already at or over budget: the hours are evidently being kept somewhere, and a prompt
      // that argues with a full bar is worse than no prompt. A burn we were not *told* (the API
      // withholds it without `time.entry.read`, #313) is not a full one — offer, or the hours
      // this prompt exists to capture are lost to a field nobody could read.
      !(burn != null && burn.remaining != null && burn.remaining <= 0),
  );

  // --- the ghost block (#335 F6) ----------------------------------------------------------- //
  // A block planned for tomorrow used to survive its task being completed today: it stayed on the
  // Agenda and in the Google mirror, while the spawned occurrence started "Nog niet ingepland."
  // The plan belonged to the work, and the work moved on. So the finish prompt names what is left
  // standing and offers to take it down — through the ordinary delete path, so the mirror is told.
  let leftoverBlocks = $state<{ id: string; start: string }[]>([]);
  let removeLeftovers = $state(true);

  let logTime = $state(false);
  let logDate = $state("");
  let logStart = $state("");
  let logEnd = $state("");
  let logDescription = $state("");
  let logScheduleId = $state("");
  const logMinutes = $derived(spanMinutes(logStart, logEnd));

  /** "HH:MM" → minutes past midnight, or null when it is not a full time yet. */
  function clockMinutes(value: string): number | null {
    const m = /^(\d{1,2}):(\d{2})$/.exec(value);
    if (!m) return null;
    const minutes = Number(m[1]) * 60 + Number(m[2]);
    return minutes >= 0 && minutes <= 24 * 60 ? minutes : null;
  }

  function clock(minutes: number): string {
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  }

  /** What the entry will be worth, so the dialog shows the number before it stores it. */
  function spanMinutes(start: string, end: string): number | null {
    const from = clockMinutes(start);
    const to = clockMinutes(end);
    if (from == null || to == null) return null;
    // The API rolls an end at or before the start forward a day, like every other entry path.
    return to > from ? to - from : 24 * 60 - from + to;
  }

  /**
   * Open the finish prompt, with the hours it can suggest for free (#314).
   *
   * In order: an unlogged block of mine whose time has passed (#188 — the hours are already
   * agreed, so the box is ticked and the block travels along to be marked logged); otherwise
   * the unspent part of the budget, laid backwards from the last quarter-hour, unticked because
   * a pre-ticked box on a number nobody agreed writes hours nobody agreed to; otherwise blank.
   *
   * Computed here rather than in a `$derived` on purpose: "has this block passed?" reads the
   * clock, and a clock read during SSR is a different answer from the same read after hydration.
   */
  function openFinishPrompt(reason: "checklist" | "status", statusKey?: string) {
    finishReason = reason;
    finishTargetKey = statusKey ?? finishStatus?.key ?? "";
    logScheduleId = "";
    logTime = false;
    logDescription = task.title;
    const now = new Date();
    const today = localDayTime(now.toISOString());
    logDate = today.day;
    logStart = "";
    logEnd = "";
    // Blocks still ahead of us, and only the ones this person may actually remove — a colleague's
    // planned afternoon is not ours to delete as a side effect of finishing the task.
    leftoverBlocks = (data.schedules ?? [])
      .filter(
        (b) =>
          new Date(b.starts_at).getTime() > now.getTime() &&
          (b.user_id === userId ? canSchedule : canScheduleAny),
      )
      .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
      .map((b) => ({ id: b.id, start: b.start }));
    removeLeftovers = true;
    if (!offerLogTime) {
      showFinishPrompt = true;
      return;
    }
    const block = (data.schedules ?? [])
      .filter(
        (b) =>
          b.user_id === userId && !b.time_entry_id && new Date(b.ends_at).getTime() < now.getTime(),
      )
      .sort((a, b) => b.starts_at.localeCompare(a.starts_at))[0];
    if (block) {
      const from = localDayTime(block.starts_at);
      const to = localDayTime(block.ends_at);
      logDate = from.day;
      logStart = from.time;
      logEnd = to.time;
      logScheduleId = block.id;
      logTime = true;
    } else if (burn != null && burn.remaining != null && burn.remaining > 0) {
      const nowMinutes = clockMinutes(today.time) ?? 0;
      const end = Math.floor(nowMinutes / 15) * 15;
      // Clamped at midnight rather than wrapped: an overnight span is a real thing the API
      // supports and never what "the rest of the budget" means. `burn.remaining` is the API's
      // own remainder (#313) — without it there is no "rest of the budget" to prefill, so the
      // span stays empty rather than guessing the whole allocation.
      const start = Math.max(0, end - burn.remaining);
      if (end - start >= 1) {
        logStart = clock(start);
        logEnd = clock(end);
      }
    }
    showFinishPrompt = true;
  }

  /**
   * Moving the status by hand into a finished state is the *other* way a task gets finished —
   * and probably the commoner one — so it gets the same offer (#314).
   *
   * It opens the prompt only when there is actually something to offer. A confirm dialog whose
   * only content is a button that repeats what the user just did is friction, and this select
   * has always been a one-click control; so where the hours cannot be logged anyway (no
   * permission, module off, budget already met) it submits exactly as it did before.
   */
  function onStatusPicked(event: Event & { currentTarget: HTMLSelectElement }) {
    const target = statuses.find((s) => s.key === statusValue);
    if (target?.is_terminal && !isDone && offerLogTime && !needsClosingMoment(target)) {
      // The prompt is what commits the move, so put the control back to what is still true —
      // through the binding, never `select.value`: an imperative assignment marks the control
      // dirty and it then keeps that value through the re-render the confirm triggers, so the
      // sidebar went on reading the old status until a hard reload.
      statusValue = task.status;
      openFinishPrompt("status", target.key);
      return;
    }
    event.currentTarget.form?.requestSubmit();
  }
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

  // Live company/project picks for the edit form (#227): the client narrows the project list
  // and a picked project backfills its client, like every create-side pairing of these two
  // pickers (time's EntryForm, the interaction forms). Re-armed from the stored task on the
  // edit-mode toggle and when navigating to another task — a mid-session reload (a comment,
  // a quick-create) must not clobber a live pick.
  // svelte-ignore state_referenced_locally
  let fCompany = $state(task.company_id ?? "");
  // svelte-ignore state_referenced_locally
  let fProject = $state(task.project_id ?? "");
  // The deadline as it stands *in the form*, not as stored: it is the repeat rule's anchor, so a
  // preview built from the stored value would answer for a date the user has already changed —
  // and the two controls now sit inches apart in the same card, which makes that impossible to
  // miss and impossible to defend.
  // svelte-ignore state_referenced_locally
  let liveDue = $state(task.due_date ?? "");
  // Same rule for the budget: it is what the auto-plan's length prefills from, and the two
  // controls are on the same screen — a prefill that read the stored value would offer an hour
  // to somebody who has just typed 1:30 two cards up.
  // svelte-ignore state_referenced_locally
  let liveAllocated = $state<number | null>(task.allocated_minutes ?? null);
  // svelte-ignore state_referenced_locally
  let pickedTaskId = task.id;
  $effect(() => {
    if (task.id !== pickedTaskId) {
      pickedTaskId = task.id;
      fCompany = task.company_id ?? "";
      fProject = task.project_id ?? "";
      liveDue = task.due_date ?? "";
      liveAllocated = task.allocated_minutes ?? null;
    }
  });
  // The client narrows the project list (above); the lifecycle then decides what is *suggested*
  // within it — a finished project sits behind the search wearing its status rather than beside
  // this week's work, and the one this task is already on is always offered, however it ended.
  const companyPicker = $derived(splitCompanyOptions(data.companies, { selectedId: fCompany }));
  const companyItems = $derived(companyPicker.live);
  const projectPicker = $derived(
    splitProjectOptions(data.projects, { companyId: fCompany, selectedId: fProject }),
  );
  const projectItems = $derived(projectPicker.live);
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
  // The assignee field is open in place (use mode): the one other moment the picker is drawn.
  let assigneeInlineOpen = $state(false);
  /** A cancelled in-place pick of the client or the project must not linger as the live pair. */
  function resetRelationPicks() {
    fCompany = task.company_id ?? "";
    fProject = task.project_id ?? "";
  }
  $effect(() => {
    const companyId = fCompany;
    if (!companyId) return;
    // Edit mode only since #453 — or the assignee field opened in place: the read view prints
    // `assignee_contact_name`, which the API resolves — a portal login cannot read `/contacts`
    // and used to see "Contactpersoon".
    if (!editMode && !assigneeInlineOpen) return;
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

  /**
   * Create-then-edit lands here to *name* the task, so the caret starts in the title and the
   * placeholder is selected: the first keystroke replaces "Naamloze taak" rather than appending
   * to it, which is the whole difference between this and a form that merely happens to be open.
   *
   * Only for a row nobody has named (`unnamed`, #350) — opening the pencil on real work must not
   * put the reader's cursor in a field they did not come to change.
   *
   * Repeated over the second after arrival, and for the same three reasons `TaskComments.reveal`
   * is: arriving here is a navigation, so SvelteKit's `reset_focus()` hands focus back to
   * `<body>` *after* we take it, and the description editor mounts asynchronously beside us. One
   * attempt loses to whichever runs last, silently. `claimedTitle` makes it once per visit, so a
   * later reload (the AI fill-in, #327) never steals a caret back.
   */
  let titleInput = $state<HTMLInputElement | null>(null);
  let claimedTitle = false;
  $effect(() => {
    if (claimedTitle || !editMode || !data.task.unnamed) return;
    claimedTitle = true;
    void (async () => {
      for (const wait of [0, 60, 200, 500]) {
        await tick();
        if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
        if (!titleInput) continue;
        if (document.activeElement === titleInput) return;
        titleInput.focus({ preventScroll: true });
        titleInput.select();
      }
    })();
  });

  // A detour that started on a client's or a project's page (#408): leaving edit mode — by
  // saving, by Annuleren, or by ⋯ → Klaar met bewerken — returns to where it started, and so does
  // Verwijderen. With no `?from=` each one behaves exactly as it did: this task, edit mode off.
  const origin = $derived(originOf(page.url));
  function leaveEdit(): void {
    // …and the marker that opened the form is consumed with it (#402) — but only on the arm that
    // stays on this page. A detour's exit replaces this URL, and its `?edit=1` goes with it.
    if (origin) void goto(origin, { invalidateAll: true });
    else {
      editMode = false;
      clearEditIntent();
    }
  }

  // --- acting on the *stored* record from inside edit mode (#335 F7) ----------------------- //
  // Create-then-edit (#230) is right: the record exists, so Inplannen is reachable without a
  // save. But the modal prefills from what is **stored**, so typing a title and a budget and then
  // pressing Inplannen booked a block called "Naamloze taak" for a default hour — and its Google
  // event too. The honest sequence was save-then-plan and nothing said so, so this does it: one
  // round trip ahead of the one the user asked for, through the same single save.
  //
  // Rejected alternative: disabling Inplannen while editing — a padlock on the thing the user is
  // most likely to want next (#253's "a control that always refuses").
  let editForm: HTMLFormElement | undefined = $state();
  let pendingSave: ((ok: boolean) => void) | null = null;

  /** Resolves once what is typed is stored — `true` when it landed, `false` when the save failed. */
  function saveIfEditing(): Promise<boolean> {
    if (!editMode || !editForm) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => {
      pendingSave = resolve;
      editForm?.requestSubmit();
    });
  }
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
  // --- the discussion (#312, and the pass that made it survive fifty comments) --------------- //
  // Threading, folding, the reading order and the `?comment=` deep link all live in
  // `TaskComments`: it is the one surface on this page that is a *feed*, and keeping it inline
  // meant every rule about it competed with the task's own form for room in one 2,500-line file.
  //
  // The order is a personal preference (`comment-prefs.ts`) held here rather than in the URL,
  // applied optimistically and saved in the background — a reorder that waits for a round trip
  // is a control that feels broken.
  let commentSort = $state<CommentSort>(readCommentSort(page.data.prefs));
  function saveCommentSort(next: CommentSort) {
    commentSort = next;
    void fetch("/set-comment-prefs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sort: next }),
    });
  }
  /** The comment a notification, a mail button or the trail sent this reader to. */
  const focusComment = $derived(page.url.searchParams.get("comment"));

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
  // single save (the API rejects an extension without one). Not on a placeholder row nobody
  // has saved yet (`unnamed`, #350): create-then-edit wrote today over it and dropped the user
  // into this form, so the first date they pick is *setting* the deadline, and the API asks
  // for no reason either — the flag clears with the save that names the task.
  const dueIsCommitted = $derived(!task.unnamed);
  let reasonModalOpen = $state(false);
  let stagedDueDate = $state("");
  let dueReason = $state("");
  let reasonDraft = $state("");
  function onDueChanged(value: string) {
    liveDue = value;
    if (dueIsCommitted && task.due_date && value && value > task.due_date) {
      stagedDueDate = value;
      reasonDraft = dueReason;
      reasonModalOpen = true;
    }
  }

  const today = orgToday();
  // The board's vocabulary, not a fifth private copy of it (#395). The card only ever shouts
  // about the two states that are claims — the moment has passed, and the moment is now — so it
  // reads the bucket rather than re-deriving "is this late".
  const bucket = $derived(isDone ? "later" : dueBucket(task.due_date, today));
  const overdue = $derived(bucket === "overdue");
  const dueToday = $derived(bucket === "today");
  // A finished task's note is the day it was finished, never a distance that keeps counting.
  const distance = $derived(
    task.due_date ? dueNote(task.due_date, today, isDone, task.completed_at) : null,
  );
  const currentLabelIds = $derived((task.labels ?? []).map((l) => l.id));

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

  /** Where an activity entry deep-links: a comment (`?comment=…`), or the contact moment a close
   *  was justified with (#157) — the interactions panel row carries `#interaction-…`.
   *
   *  A bare `#comment-<id>` was right while every comment was on the page and became a link that
   *  did nothing the moment the list learned to fold. `?comment=` is the same destination the
   *  notification inbox and the mail button now use, and the section expands whatever hides the
   *  message before scrolling to it. It costs no round trip: this page's load never reads
   *  `event.url`, so SvelteKit does not rerun it for a query-string change. */
  function activityHref(a: { payload: Record<string, unknown> }): string | null {
    const commentId = a.payload.comment_id ? String(a.payload.comment_id) : null;
    if (commentId) {
      return (task.comments ?? []).some((c) => c.id === commentId) ? `?comment=${commentId}` : null;
    }
    if (a.payload.closing_interaction_id) return `#interaction-${a.payload.closing_interaction_id}`;
    // A mirrored contact-moment milestone (#152) links to the moment in the interactions panel.
    if (a.payload.interaction_id) return `#interaction-${a.payload.interaction_id}`;
    // Both ends of a recurrence hand-off (#335 F5). The clone has always carried
    // `source_task_id` and this function never handled it, so even the one line that *was*
    // written was a dead end; the carrier now carries `next_task_id` and links the other way.
    if (a.payload.next_task_id) return `/tasks/${a.payload.next_task_id}`;
    if (a.payload.source_task_id) return `/tasks/${a.payload.source_task_id}`;
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
    // "volgende taak aangemaakt (13 sep)" — dated, so the trail line is checkable without
    // following the link (#335 F5).
    if (a.action === "recurrence_spawned_next") {
      return t("tasks.activity.recurrence_spawned_next", {
        date: a.payload.due_date ? fmtDayMonth(String(a.payload.due_date)) : "—",
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
    if (a.action === "attachment_visibility_changed") {
      return t(
        a.payload.client_visible
          ? "tasks.activity.attachment_shown_to_client"
          : "tasks.activity.attachment_hidden_from_client",
        { filename: String(a.payload.filename ?? "") },
      );
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

<!--
  One column, one order, at every width (#335 F9).

  The 320px rail held six rows and a lot of empty space in use mode, and in edit mode it put the
  eye path through title (left) → fields (right) → checklists (left) → Herhaling and Opslaan
  (bottom right, below Labels). A phone was already one column, in a *different* order, with the
  save button stranded in the middle of the form. So: one column everywhere, the same order
  everywhere, and the six rail rows become one property band under the title.

  `max-w-4xl` rather than the shell's 1600px measure: that width was chosen against a ten-column
  client list, and a single column of prose and form rows read across it is unreadable. A screen
  opts out of the shell measure by narrowing itself, never by widening it for everyone.
-->
<div class="mx-auto w-full min-w-0 max-w-4xl space-y-4">
  <!-- "schakl leest de e-mail" (#327). Above the card rather than inside it: it is about the whole
       task, it is short-lived, and it must not push the title around while it comes and goes. -->
  {#if task.ai_status}
    <TaskAIStatus taskId={task.id} status={task.ai_status} editing={editMode} />
  {/if}

  <!-- Header — what this task is, and what is true of it at a glance. Always first, and not
       in the ordered list below: it is the page's title, not a section of it. -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <div class="flex items-start gap-3">
      {#if editMode}
        <input
          name="title"
          value={task.title}
          required
          form="task-edit"
          bind:this={titleInput}
          class="w-full flex-1 rounded-lg border border-border p-2 text-xl font-semibold text-text outline-none focus:border-brand"
        />
      {:else}
        <!-- 20 px, the one page-title size (#404's scale). It was 18 px here and 20 px on the
             other 97 H1s in the app — a page title that shrinks when you open a record is a
             hierarchy the reader has to re-learn per screen. -->
        <h1
          class="flex-1 text-xl font-semibold {isDone
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
                      // "Klaar" is an assertion that the work is done, so it commits it (#409).
                      // Flipping the flag was a second Annuleren under the opposite word: the
                      // page left edit mode, the header showed the stored title again, and
                      // nothing said the save had not happened — the kebab sits at the top of a
                      // whole-page edit surface whose one save is at the bottom, so reaching for
                      // the control nearest the field you just changed is what lost the change.
                      // `requestSubmit` rather than `submit` so the title's `required` is checked
                      // and `use:enhance` runs; that handler closes edit mode on success and
                      // keeps it open on a validation failure, with the error shown.
                      if (editMode) {
                        if (!busy.is("update")) editForm?.requestSubmit();
                        return;
                      }
                      // Re-arm the relation picks so a stale pick never overrides the stored
                      // relation on a later edit session.
                      fCompany = task.company_id ?? "";
                      fProject = task.project_id ?? "";
                      // Opening only — the leaving half returned above. So this is no longer a
                      // toggle, and neither of the two things that used to ride on its false arm
                      // is dropped: the submit runs `use:enhance`, whose handler consumes the
                      // `?edit=1` marker (#402) and returns to the detour's origin (#408) on the
                      // save that closes the mode. "Klaar met bewerken" therefore now saves *and*
                      // lands back on the client you opened the task from, which is both issues'
                      // answer to the same gesture.
                      editMode = true;
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
        <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
          >{label.name}</span
        >
      {/each}
      {#if overdue || dueToday}
        <!-- "Vandaag" is a state too, and it was the one the card could not say: a task due in
             four hours looked exactly like one due in September (#395). It is the palette's
             chip, so it reads the same here as the section heading the board files it under. -->
        <StateMark
          state={overdue ? "late" : "today"}
          variant="chip"
          label={t(overdue ? "tasks.due.overdue" : "tasks.due.today")}
        />
      {/if}
      <!-- The rule, readable. "↻ Maandelijks" was every word the page had ever said about a
             stored recurrence: no interval, no anchor, no mode, and no next date at all — the
             one it could not have shown, because `recurrence_next_run` was stored and exposed to
             nobody (#335 F3). Compact here; the Planning card below spells the mode out. -->
      {#if recurrence}
        <a
          href="#planning"
          class="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted hover:text-brand"
        >
          ↻ {recurrenceSentence(recurrence, { compact: true })}{task.recurrence_next_run
            ? ` · ${fmtDayMonth(task.recurrence_next_run)}`
            : ""}
        </a>
      {/if}
      {#if editMode}
        <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
          >{t("tasks.detail.edit_mode")}</span
        >
      {/if}
    </div>
  </section>

  <!--
    Every section below is a snippet, and `SECTION_POSITIONS` decides where each one lands
    among the panels enabled modules contribute (#393). The declaration order here is
    irrelevant; the array in the script is the page.
  -->

  {#snippet properties()}
    <!-- Properties — the rail's six rows, in one band under the title, where the phone flow
         already put them. A responsive grid rather than a stack: nine one-line facts read as a
         band and as nine stacked rows they read as a wall. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-3 {PANEL_HEADING}">
        {t("tasks.detail.properties")}
      </h3>
      <div class="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        <!-- Status. In **use** mode it stays the instant one-click control it has always been; in
             edit mode it joins `task-edit` like every field around it, so the card stops running
             one-and-a-half save models at once (#335 F8, docs/UX.md's one-save rule). -->
        <div>
          <label for="status" class="mb-1 block text-xs font-medium text-text-muted"
            >{t("tasks.field.status")}</label
          >
          {#if !canEditTask}
            <p id="status" class="text-sm text-text">
              {statuses.find((s) => s.key === task.status)?.name ?? task.status}
            </p>
          {:else if editMode}
            <select id="status" name="status" form="task-edit" class={inputClass}>
              {#each statuses as s (s.key)}
                <option value={s.key} selected={task.status === s.key}>{s.name}</option>
              {/each}
            </select>
          {:else}
            <form method="POST" action="?/update" use:enhance={busy.keep("status")}>
              <select
                id="status"
                name="status"
                class={inputClass}
                bind:value={statusValue}
                onchange={onStatusPicked}
              >
                {#each statuses as s (s.key)}
                  <option value={s.key}>{s.name}</option>
                {/each}
              </select>
            </form>
          {/if}
        </div>

        <!-- The client comes before the two fields it narrows: the contact half of the assignee
             picker, and the project list. In use mode every property below is edited in place
             (`InlineField`): one field, its own save, the page's own `?/update` — the same shape
             the description got in #455, because moving a deadline or handing a task to a
             colleague should not cost the pencil and a save at the foot of the page. -->
        {#if editMode}
          <div>
            <label for="company" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.company")}</label
            >
            <Combobox
              items={companyItems}
              archived={companyPicker.retired}
              archivedLabel={companyArchivedLabel()}
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
        {:else}
          <InlineField
            id="company"
            label={t("tasks.field.company")}
            canEdit={canEditTask}
            onclose={resetRelationPicks}
          >
            {#snippet read()}
              <p class="truncate text-sm text-text">
                {#if task.company_id}
                  <a href={`/companies/${task.company_id}`} class="hover:text-brand"
                    >{companyName(task.company_id) ?? "—"}</a
                  >
                {:else}—{/if}
              </p>
            {/snippet}
            {#snippet editor()}
              <Combobox
                items={companyItems}
                archived={companyPicker.retired}
                archivedLabel={companyArchivedLabel()}
                name="company_id"
                value={fCompany}
                id="company"
                onselect={onCompanyPicked}
                oncreate={(name) => {
                  qcCompanyName = name;
                  qcCompanyOpen = true;
                }}
              />
              <!-- The pair travels together: a project of another client is dropped by the pick
                   above, exactly as edit mode does, and the API stores the pair as given. -->
              <input type="hidden" name="project_id" value={fProject} />
            {/snippet}
          </InlineField>
        {/if}

        {#if editMode}
          <div>
            <label for="assignee-employees" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.assignees")}</label
            >
            <!-- Employees (#375), or — when the task has a client (#273) — one of that client's
                 contacts. -->
            <TaskAssigneePicker
              formId="task-edit"
              employees={data.members}
              contacts={assigneeContacts}
              contactsEnabled={!!fCompany}
              assignees={task.assignees ?? []}
              contactValue={task.assignee_contact_id ?? ""}
            />
          </div>
        {:else}
          <InlineField
            id="assignee-employees"
            label={t("tasks.field.assignees")}
            canEdit={canEditTask}
            onopen={() => (assigneeInlineOpen = true)}
            onclose={() => (assigneeInlineOpen = false)}
          >
            {#snippet read()}
              {#if task.assignee_contact_id}
                <p class="text-sm text-text">
                  {task.assignee_contact_name ??
                    contactName(task.assignee_contact_id) ??
                    t("party.contact")}
                  <span class="text-xs text-text-muted">({t("party.contact")})</span>
                </p>
              {:else if (task.assignees ?? []).length > 0}
                <!-- The whole roster, not the star alone: `max` is high because this is the
                     record's own page, where "who is on this" is the question, not a column
                     with 180px to spend. -->
                <Assignees assignees={task.assignees ?? []} members={data.members} max={8} />
              {:else}
                <p class="text-sm text-text">—</p>
              {/if}
            {/snippet}
            {#snippet editor()}
              <TaskAssigneePicker
                employees={data.members}
                contacts={assigneeContacts}
                contactsEnabled={!!task.company_id}
                assignees={task.assignees ?? []}
                contactValue={task.assignee_contact_id ?? ""}
              />
            {/snippet}
          </InlineField>
        {/if}

        {#if editMode}
          <div>
            <label for="project" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.project")}</label
            >
            <Combobox
              items={projectItems}
              archived={projectPicker.retired}
              archivedLabel={projectArchivedLabel()}
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
        {:else}
          <InlineField
            id="project"
            label={t("tasks.field.project")}
            canEdit={canEditTask}
            onclose={resetRelationPicks}
          >
            {#snippet read()}
              <p class="truncate text-sm text-text">
                {#if task.project_id}
                  <a href={`/projects/${task.project_id}`} class="hover:text-brand"
                    >{projectName(task.project_id) ?? "—"}</a
                  >
                {:else}—{/if}
              </p>
            {/snippet}
            {#snippet editor()}
              <Combobox
                items={projectItems}
                archived={projectPicker.retired}
                archivedLabel={projectArchivedLabel()}
                name="project_id"
                value={fProject}
                id="project"
                onselect={onProjectPicked}
                oncreate={(name) => {
                  qcProjectName = name;
                  qcProjectOpen = true;
                }}
              />
              <!-- A picked project backfills its client, as everywhere the pair is picked. -->
              <input type="hidden" name="company_id" value={fCompany} />
            {/snippet}
          </InlineField>
        {/if}

        {#if editMode}
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
        {:else}
          <InlineField
            id="priority"
            label={t("tasks.field.priority")}
            canEdit={canEditTask}
            saveOnChange
          >
            {#snippet read()}
              <p class="text-sm text-text">{t(`tasks.priority.${task.priority}`)}</p>
            {/snippet}
            {#snippet editor({ submit })}
              <select id="priority" name="priority" class={inputClass} onchange={submit}>
                {#each priorities as p (p)}
                  <option value={p} selected={task.priority === p}
                    >{t(`tasks.priority.${p}`)}</option
                  >
                {/each}
              </select>
            {/snippet}
          </InlineField>
        {/if}

        <!-- Not for a client (#449): the estimate is the agency's, the API blanks it, and a
             dash headed "Tijdbudget" is a question the client should not be holding. -->
        <div class:hidden={isPortal}>
          {#if editMode}
            <label for="allocated" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.allocated_input")}</label
            >
            <DurationInput
              id="allocated"
              name="allocated_minutes"
              formId="task-edit"
              minutes={task.allocated_minutes ?? null}
              onchange={(minutes) => (liveAllocated = minutes)}
              class={inputClass}
            />
          {:else}
            <InlineField
              id="allocated"
              label={t("tasks.field.allocated")}
              canEdit={canEditTask}
              onclose={() => (liveAllocated = task.allocated_minutes ?? null)}
            >
              {#snippet read()}
                {#if burn}
                  <!-- The figure opens the hours behind it (#443, Principle 7) — only for a
                       viewer /overview will let in, never a link that bounces (#253). -->
                  <BudgetBar
                    spent={burn.spent}
                    budget={burn.budget}
                    remainingText={burn.remainingText}
                    spentText={burn.spentText}
                    href={can(page.data.user, "time.report.read")
                      ? `/overview?task_id=${task.id}`
                      : undefined}
                  />
                {:else}
                  <p class="text-sm tabular-nums text-text">
                    {task.allocated_minutes ? formatMinutes(task.allocated_minutes) : "—"}
                  </p>
                {/if}
              {/snippet}
              {#snippet editor()}
                <DurationInput
                  id="allocated"
                  name="allocated_minutes"
                  minutes={task.allocated_minutes ?? null}
                  onchange={(minutes) => (liveAllocated = minutes)}
                  class={inputClass}
                />
              {/snippet}
            </InlineField>
          {/if}
        </div>

        {#if !isPortal}
          <!-- Staff-only: a client reading their own task learns nothing from "yes, you can see
               this", and the icon's meaning lives in a `title=` a phone cannot show. Full width
               only while editing, where it is a checkbox carrying a line of explanation; as a
               two-word read state it is an ordinary cell and a full row of it is a hole. -->
          <div class={editMode ? "sm:col-span-2 lg:col-span-3" : ""}>
            {#if editMode}
              <!-- Hidden "false" precedes the checkbox so an unchecked box still submits a value;
                   the use-mode status quick-form carries neither and leaves both untouched. -->
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
            {:else}
              <InlineField
                id="visible_to_client"
                label={t("tasks.field.visible_to_client")}
                canEdit={canEditTask}
                saveOnChange
              >
                {#snippet read()}
                  <p class="flex items-center gap-1.5 text-sm text-text">
                    <ClientVisibilityIcon
                      visible={task.visible_to_client}
                      companyId={task.company_id}
                      projectId={task.project_id}
                      size={13}
                    />
                    {task.visible_to_client ? t("common.yes") : t("common.no")}
                  </p>
                {/snippet}
                {#snippet editor({ submit })}
                  <input type="hidden" name="visible_to_client" value="false" />
                  <label class="flex items-start gap-2 text-sm text-text">
                    <FormCheckbox
                      id="visible_to_client"
                      name="visible_to_client"
                      value="true"
                      checked={task.visible_to_client}
                      class="mt-0.5 shrink-0"
                      onchange={submit}
                    />
                    <span>
                      <span class="font-medium">{t("tasks.field.visible_to_client")}</span>
                      <span class="mt-0.5 block text-[11px] leading-snug text-text-muted"
                        >{t("tasks.field.visible_to_client_hint")}</span
                      >
                    </span>
                  </label>
                {/snippet}
              </InlineField>
            {/if}
          </div>
        {/if}

        <div class={editMode ? "sm:col-span-2 lg:col-span-3" : ""}>
          {#if editMode}
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
          {:else if task.requires_interaction || canEditTask}
            <!-- Read-only, the cell exists only when the policy is on; an editor sees it either
                 way, because "off" is the state they switch it from. -->
            <InlineField
              id="requires_interaction"
              label={t("tasks.field.requires_interaction")}
              canEdit={canEditTask}
              saveOnChange
            >
              {#snippet read()}
                {#if task.requires_interaction}
                  <span
                    class="inline-block rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                  >
                    {t("tasks.field.requires_interaction_badge")}
                  </span>
                {:else}
                  <p class="text-sm text-text">{t("common.no")}</p>
                {/if}
              {/snippet}
              {#snippet editor({ submit })}
                <input type="hidden" name="requires_interaction" value="false" />
                <label class="flex items-start gap-2 text-sm text-text">
                  <FormCheckbox
                    id="requires_interaction"
                    name="requires_interaction"
                    value="true"
                    checked={task.requires_interaction}
                    class="mt-0.5 shrink-0"
                    onchange={submit}
                  />
                  <span>
                    <span class="font-medium">{t("tasks.field.requires_interaction")}</span>
                    <span class="mt-0.5 block text-[11px] leading-snug text-text-muted"
                      >{t("tasks.field.requires_interaction_hint")}</span
                    >
                  </span>
                </label>
              {/snippet}
            </InlineField>
          {/if}
        </div>

        <!-- Labels: the chips already sit under the title in use mode, so only the picker lives
             here — and it lives *in* the properties band now instead of a card of its own above
             the repeat rule, which is how the least important thing on the page came to sit above
             the most easily misread one (#335 F9). -->
        {#if editMode}
          <div class="sm:col-span-2 lg:col-span-3">
            <div class="mb-1 flex items-center justify-between">
              <span class="text-xs font-medium text-text-muted">{t("tasks.field.labels")}</span>
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
                class="space-y-1 rounded-lg border border-border p-3"
              >
                {#each data.labels as label (label.id)}
                  <label
                    class="flex items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface"
                  >
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
                <Button size="sm" loading={busy.is("setLabels")} class="mt-2"
                  >{t("common.apply")}</Button
                >
              </form>

              <!-- Ticking labels onto this task is `tasks.task.write`; *minting* one adds a row to
                   the org's vocabulary and is `tasks.label.write`, which only an admin holds. -->
              {#if canWriteLabels}
                <form
                  method="POST"
                  action="?/createLabel"
                  use:enhance={busy.wrap("createLabel", () => ({ update }) => {
                    showLabelPicker = false;
                    void update();
                  })}
                  class="mt-2 rounded-lg border border-dashed border-border p-3"
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
                    class="mt-2"
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
          </div>
        {:else if canEditTask}
          <!-- In use mode the chips already sit under the title; this cell is where a writer
               changes them without leaving the page. Posts `?/setLabels`, the whole set at once,
               and minting a new label (`tasks.label.write`) is a second form *beside* it. -->
          <InlineField
            id="labels"
            label={t("tasks.field.labels")}
            action="?/setLabels"
            canEdit={canEditTask}
            class="sm:col-span-2 lg:col-span-3"
          >
            {#snippet read()}
              {#if (task.labels ?? []).length === 0}
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
            {/snippet}
            {#snippet editor()}
              <div class="space-y-1 rounded-lg border border-border p-3">
                {#each data.labels as label (label.id)}
                  <label
                    class="flex items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface"
                  >
                    <FormCheckbox
                      name="label_ids"
                      value={label.id}
                      checked={currentLabelIds.includes(label.id)}
                      class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
                    />
                    <span class="h-2.5 w-2.5 rounded-full {labelDotClass(label.color)}"></span>
                    <span class="text-text">{label.name}</span>
                  </label>
                {:else}
                  <p class="text-sm text-text-muted">{t("tasks.labels.empty")}</p>
                {/each}
              </div>
            {/snippet}
            {#snippet after({ close })}
              {#if canWriteLabels}
                <form
                  method="POST"
                  action="?/createLabel"
                  use:enhance={busy.wrap("createLabel", () => ({ update }) => {
                    close();
                    void update();
                  })}
                  class="mt-2 rounded-lg border border-dashed border-border p-3"
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
                    class="mt-2"
                  >
                    {t("tasks.labels.create")}
                  </Button>
                </form>
              {/if}
            {/snippet}
          </InlineField>
        {/if}
      </div>
    </section>
  {/snippet}

  {#snippet planning()}
    <!-- Planning — the one place that answers "when", and it sits *after* the description and
         the checklists (#393): a colleague reads what has to happen before reading when it is
         due. The deadline, the blocks and the repeat rule were three unrelated widgets in
         three parts of the page (#335): Vervaldatum in the
         details card, Planning in the main column, Herhaling at the very bottom of the rail below
         Labels. They are one subject. The mode split still holds (docs/UX.md §3): blocks are use
         mode, the deadline and the rule are definition and get their inputs behind the pencil —
         but their read state is always here, instead of a chip in one place and nothing anywhere
         for the rule. -->
    <section id="planning" class="rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-3 {PANEL_HEADING}">
        {t("tasks.detail.planning")}
      </h3>

      <div class="space-y-4">
        <!-- Deadline -->
        <div>
          {#if editMode}
            <label for="due_date" class="mb-1 block text-xs font-medium text-text-muted"
              >{t("tasks.field.due_date")}</label
            >
            <div class="max-w-xs">
              <DateInput
                id="due_date"
                name="due_date"
                value={task.due_date ?? ""}
                formId="task-edit"
                required
                onchange={onDueChanged}
              />
            </div>
            <!-- A hint that promises a question the form will not ask is half a sentence: a
                 placeholder row's first date is set, not moved. -->
            {#if dueIsCommitted}
              <p class="mt-1 text-[11px] text-text-muted">{t("tasks.detail.due_reason_hint")}</p>
            {/if}
            <!-- Rows written before #392 open, render and edit exactly as before — but saving
                 one asks for the date it never had, which is the way out rather than a refusal. -->
            {#if !task.due_date}
              <p class="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
                {t("tasks.detail.due_required_hint")}
              </p>
            {/if}
          {:else}
            <!-- Moved in place: the date, its reason when it is an extension (the same modal edit
                 mode uses, and the same hidden field), and the API's refusal beside the field. -->
            <InlineField
              id="due_date"
              label={t("tasks.field.due_date")}
              canEdit={canEditTask}
              class="max-w-xs"
              onclose={() => {
                liveDue = task.due_date ?? "";
                dueReason = "";
              }}
            >
              {#snippet read()}
                <p
                  class="text-sm tabular-nums {overdue
                    ? 'font-semibold text-red-600 dark:text-red-400'
                    : 'text-text'}"
                >
                  {task.due_date ? fmtDayMonthYear(task.due_date) : "—"}
                  {#if distance && "on" in distance}
                    <span
                      class="text-xs font-normal text-text-muted"
                      title={fmtDateTime(distance.on)}
                      >{t(distance.key, { date: fmtDayMonth(distance.on) })}</span
                    >
                  {:else if distance}
                    <!-- The distance, muted: a date on its own asks the reader to subtract
                         (#395). -->
                    <span class="text-xs font-normal text-text-muted"
                      >{t(distance.key, { count: distance.count })}</span
                    >
                  {/if}
                </p>
              {/snippet}
              {#snippet editor()}
                <DateInput
                  id="due_date"
                  name="due_date"
                  value={task.due_date ?? ""}
                  required
                  onchange={onDueChanged}
                />
                <input type="hidden" name="due_change_reason" value={dueReason} />
                {#if dueIsCommitted}
                  <p class="text-[11px] text-text-muted">{t("tasks.detail.due_reason_hint")}</p>
                {/if}
              {/snippet}
            </InlineField>
          {/if}
        </div>

        <!-- Planned blocks on the calendar (#188) — schedule, move, and log time from a passed one.
             Rendered bare: it is a part of this card now, not a card beside it. -->
        <div class="border-t border-border pt-4">
          <TaskSchedulePanel
            bare
            schedules={data.schedules}
            task={{
              id: task.id,
              title: task.title,
              project_id: task.project_id,
              company_id: task.company_id,
              assignee_user_id: task.assignee_user_id,
              assignees: task.assignees,
              allocated_minutes: task.allocated_minutes,
              due_date: task.due_date,
            }}
            members={data.members}
            currentUserId={page.data.user?.id ?? ""}
            canWrite={canSchedule}
            {canScheduleAny}
            preparing={busy.is("update")}
            beforeOpen={saveIfEditing}
          />
        </div>

        <!-- The repeat rule. In use mode it renders only when there *is* one: "Herhaling: herhaalt
             niet" is the empty structural section docs/UX.md §3 keeps out of use mode, and the
             editor behind the pencil is where a rule gets made. -->
        {#if editMode || recurrence || canEditTask}
          <div class="border-t border-border pt-4">
            {#if editMode}
              <RecurrenceEditor
                formId="task-edit"
                previewUrl={`/tasks/${task.id}/recurrence-preview`}
                {recurrence}
                dueDate={liveDue}
                allocatedMinutes={liveAllocated}
                {lastBlockStart}
                members={data.members}
                currentUserId={page.data.user?.id ?? ""}
                {canSchedule}
                {canScheduleAny}
              />
            {:else}
              <!-- In place too, through `?/setRecurrence` — the rule alone, nothing else touched.
                   A writer sees the row even without a rule ("Herhaalt niet"), because that is the
                   state a rule is made from; a reader still sees it only when there is one. -->
              <InlineField
                id="recurrence"
                label={t("tasks.recurrence.title")}
                action="?/setRecurrence"
                canEdit={canEditTask}
                labelledEditor
              >
                {#snippet read()}
                  {#if recurrence}
                    <p class="text-sm text-text">↻ {recurrenceSentence(recurrence)}</p>
                    <p class="mt-0.5 text-[11px] text-text-muted">
                      {#if task.recurrence_next_run}
                        {t("tasks.recurrence.next")}: {fmtDayMonthYear(
                          task.recurrence_next_run,
                        )}{recurrence.plan ? ` · ${planSummary(recurrence)}` : ""}
                      {:else}
                        {t("tasks.recurrence.next_on_completion")}{recurrence.plan
                          ? ` · ${planSummary(recurrence)}`
                          : ""}
                      {/if}
                    </p>
                  {:else}
                    <p class="text-sm text-text-muted">{t("tasks.recurrence.none")}</p>
                  {/if}
                {/snippet}
                {#snippet editor({ formId })}
                  <RecurrenceEditor
                    {formId}
                    previewUrl={`/tasks/${task.id}/recurrence-preview`}
                    {recurrence}
                    dueDate={task.due_date ?? ""}
                    allocatedMinutes={task.allocated_minutes ?? null}
                    {lastBlockStart}
                    members={data.members}
                    currentUserId={page.data.user?.id ?? ""}
                    {canSchedule}
                    {canScheduleAny}
                  />
                {/snippet}
              </InlineField>
            {/if}
          </div>
        {/if}
      </div>
    </section>
  {/snippet}

  {#snippet description()}
    <!-- Description — the first thing said about the work itself. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h3 class="mb-2 {PANEL_HEADING}">
        {t("tasks.field.description")}
      </h3>
      {#if editMode}
        <RichTextEditor
          name="description"
          form="task-edit"
          rows={4}
          value={task.description ?? ""}
          scope={candidateScope}
          upload={{ entityType: "task", entityId: task.id }}
        />
      {:else}
        <!-- Edited in place (#455): the one field people change ten times a day should not cost
             ⋯ → Bewerken and a save at the foot of the page. Posts `description` alone to
             `?/update`, which patches only what the form carries. -->
        <InlineText
          name="description"
          value={task.description ?? ""}
          placeholder={t("tasks.detail.description_placeholder")}
          canEdit={canEditTask}
          scope={candidateScope}
          images
          upload={{ entityType: "task", entityId: task.id }}
          id="task-description-inline"
        />
      {/if}
    </section>
  {/snippet}

  {#snippet checklists()}
    <!-- Checklists. Ticking and quick-adding items is "using" (docs/UX.md §3, §5); creating,
           renaming or deleting a checklist is structure and lives in edit mode. A task without
           checklists shows no section at all until you edit — an empty card with a create form
           is exactly the clutter use mode exists to avoid. -->
    {#if (task.checklists ?? []).length > 0 || editMode}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h3 class="mb-3 {PANEL_HEADING}">
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
                                openFinishPrompt("checklist");
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
                <!-- Quick-add is a task write (POST item); hidden from a read-only portal client (#244).
                     `clearAndFocus`, not `clear`: this row is typed into in runs, and a successful
                     action ends with SvelteKit focusing the body, so Enter used to add the item and
                     then drop the cursor (#367). -->
                <form
                  method="POST"
                  action="?/addItem"
                  use:enhance={busy.clearAndFocus(`addItem:${checklist.id}`, "title")}
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
  {/snippet}

  {#snippet links()}
    <!-- Links & attachments. Use mode shows what is attached (open, download); adding a link,
           uploading a file and deleting either are edit-mode work (docs/UX.md §3). No links and
           no files → no section, until you edit. -->
    {#if (task.links ?? []).length > 0 || data.files.length > 0 || editMode || canWriteFile}
      <!-- A register (#404): where the files are is looked up when somebody needs a file, and
           it is never the news on a task. Under a rule rather than in the eighth bordered box —
           and the Drive card directly under it declares the same, so the two now read as one
           reference band without either page or module having to arrange that. -->
      <Card kind="register">
        <!-- Drive now sits directly under this card (#393), and to a reader the two are one
             idea: "waar staan de bestanden van deze taak". They are not the same thing — these
             bytes live here and a Drive row is a reference into somebody else's system, which
             is why deleting means something different in each — so the heading gets one line
             saying which is which. Merging them would make that difference unsayable. -->
        <div class="mb-3">
          <h3 class={PANEL_HEADING}>
            {t("tasks.links.title")}
          </h3>
          <p class="text-[11px] text-text-muted">{t("tasks.links.stored_here")}</p>
        </div>
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

        {#if data.files.length > 0 || canWriteFile}
          <!-- Document uploads through the storage core (#123). Attaching is *use-mode* work,
               like a comment: a screenshot is evidence of what happened on the task, not a change
               to its definition, and a drop target that only exists after ⋯ → Bewerken is the
               three-step route this strip exists to remove (docs/UX.md). Gated on the key the
               API checks, never on `!isPortal`: a client holds no `files.file.write` and the API
               hands it only the files ticked visible. -->
          <div
            class={(task.links ?? []).length > 0 || editMode
              ? "mt-4 border-t border-border pt-4"
              : ""}
          >
            <FileAttachments
              files={data.files}
              uploadAction="?/uploadFile"
              deleteAction="?/deleteFile"
              visibilityAction="?/setFileVisibility"
              error={form?.fileError ?? null}
              readonly={!canWriteFile}
            />
          </div>
        {/if}
      </Card>
    {/if}
  {/snippet}

  {#snippet comments()}
    <!-- The discussion. Its own component (#312 follow-up): threading, the reading order, the
         folds and the `?comment=` deep link are one set of rules, and they were competing with
         the task's own edit form for room in one file. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <TaskComments
        upload={{ entityType: "task", entityId: task.id }}
        comments={task.comments ?? []}
        truncated={task.comments_truncated ?? false}
        members={data.members}
        {userId}
        {canComment}
        canDeleteAny={canDeleteAnyComment}
        scope={candidateScope}
        {busy}
        {askDelete}
        sort={commentSort}
        focusId={focusComment}
        onsort={saveCommentSort}
      />
    </section>
  {/snippet}

  {#snippet activity()}
    <!-- Activity — the staff paper trail, never a portal surface. -->
    {#if !isPortal}
      <!-- The trail is the quietest thing on the page and hangs last (docs/UX.md Principle 4).
           A register, therefore — it was the same white box as the checklist above it. -->
      <Card kind="register" level={3} title={t("tasks.activity.title")}>
        {#if activities.length === 0}
          <p class="text-sm text-text-muted">—</p>
        {:else}
          <PanelRows rows={activities} collapsed={ACTIVITY_COLLAPSED}>
            {#snippet children(shown)}
              <ul class="space-y-2">
                {#each shown as activity (activity.id)}
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
                        <a class="hover:text-brand hover:underline" {href}
                          >{activityText(activity)}</a
                        >
                      {:else}
                        {activityText(activity)}
                      {/if}
                    </span>
                  </li>
                {/each}
              </ul>
            {/snippet}
          </PanelRows>
        {/if}
      </Card>
    {/if}
  {/snippet}

  <!-- The page, in one ordered list: its own sections interleaved with the panels the
       enabled modules contribute, each at the `position` it declares. -->
  {#each orderedSections as item (item.key)}
    {#if item.kind === "panel"}
      {@const spec = panelSpec(item.panel.key)}
      {#if spec}
        {@const PanelComponent = spec.component}
        <!-- A contributed panel says what it *is* on this host (#404). Drive and contactmomenten
             declare `register` for a task, so they are drawn under a hairline rule instead of as
             two more bordered boxes among eight — which is most of the reason an empty task page
             ran 1500 px tall with nothing on it. -->
        <Card
          kind={spec.prominence === "register" ? "register" : "panel"}
          level={3}
          title={t(item.panel.titleKey)}
        >
          <PanelComponent data={item.panel.data} context={data.context} lookups={panelLookups} />
        </Card>
      {/if}
    {:else}
      {@const render = {
        properties,
        description,
        checklists,
        planning,
        links,
        comments,
        activity,
      }[item.key]}
      {@render render()}
    {/if}
  {/each}

  <!--
    The one save for the whole edit mode — sticky, so it is reachable from anywhere on the page.

    It used to sit bottom-right under Labels on a desktop and *mid-page* on a phone, where the
    button visually ended the form while half the edit surface (planning, checklists, links)
    carried on below it (#335 F9). One save button per editing surface is docs/UX.md's rule; this
    is that rule made visible. Inputs across the page join it by `form="task-edit"`.
  -->
  {#if editMode}
    <form
      id="task-edit"
      bind:this={editForm}
      method="POST"
      action="?/update"
      use:enhance={busy.wrap("update", () => async ({ update, result }) => {
        // A save that was only a means to an end (#335 F7 — pressing Inplannen while editing)
        // keeps edit mode open: the user asked to plan, not to stop editing. That is also why the
        // detour's exit (#408) is skipped for one: leaving now would abandon the act the save was
        // in service of.
        const waiting = pendingSave;
        pendingSave = null;
        if (result.type === "success" && !waiting && origin) {
          dueReason = "";
          return void goto(origin, { invalidateAll: true });
        }
        if (result.type === "success") {
          editMode = waiting !== null;
          // …and the marker that opened it goes with it (#402). A task created from a client
          // lands here as `?edit=1`, and leaving the mode while the URL still says otherwise
          // means the next visit — a reload, the back button off the client's page — reopens
          // the form over a save that had already happened. An intent is consumed once. The
          // detour's exit above needs none of this: it leaves this URL behind entirely.
          if (!editMode) clearEditIntent();
        }
        dueReason = "";
        await update();
        waiting?.(result.type === "success");
      })}
      class="sticky bottom-0 z-10 -mx-1 flex flex-wrap items-center justify-end gap-3 border-t border-border bg-surface/90 px-1 py-3 backdrop-blur"
    >
      <input type="hidden" name="due_change_reason" value={dueReason} />
      <span class="mr-auto text-xs text-text-muted">{t("tasks.detail.edit_mode")}</span>
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={leaveEdit}
      >
        {t("common.cancel")}
      </button>
      <Button loading={busy.is("update")}>{t("common.save")}</Button>
    </form>
  {/if}
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
  action={withOrigin("?/delete", page.url)}
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

<!-- The task is being finished — the last to-do was ticked, or a finished status was picked by
     hand. Offer to move it along and, in the same confirm, to record the hours it took (#314);
     or, when finishing is gated on a closing contact moment (#157), say exactly that instead of
     offering a doomed move. Never a second modal stacked on the first: finishing stays one
     confirm, which is the whole reason the hours get written down at all. -->
<Modal
  bind:open={showFinishPrompt}
  title={finishReason === "status"
    ? t("tasks.finish_prompt.title_status")
    : t("tasks.finish_prompt.title")}
>
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
    <form
      method="POST"
      action="?/update"
      use:enhance={busy.wrap("finish", () => ({ update }) => {
        showFinishPrompt = false;
        // One-shot: the dialog closes and the page reloads the finished task, so there is
        // nothing left to keep. Stated rather than inherited (docs/UX.md, forms:check).
        return update({ reset: true });
      })}
    >
      <p class="text-sm text-text">
        {finishReason === "status"
          ? t("tasks.finish_prompt.message_status", { status: finishTarget?.name ?? "" })
          : t("tasks.finish_prompt.message", { status: finishTarget?.name ?? "" })}
      </p>
      <input type="hidden" name="status" value={finishTarget?.key ?? ""} />

      <!-- The good news, when the rule already handled the next one (#335 phase 5). Said here
           because this is the moment the hand-off happens and the only moment somebody is
           watching it. -->
      {#if recurrence?.plan && recurrence.mode === "after_completion"}
        <p class="mt-3 rounded-lg bg-surface px-3 py-2 text-sm text-text">
          {t("tasks.finish_prompt.next_planned", { summary: planSummary(recurrence) })}
        </p>
      {/if}

      <!-- Blocks still standing on a calendar for work that is about to be done (#335 F6).
           Named with their dates and removable in the same confirm — a checkbox that deletes
           something has to say exactly what. -->
      {#if leftoverBlocks.length > 0}
        <div class="mt-3 rounded-lg border border-border bg-surface p-3">
          <label class="flex items-start gap-2 text-sm text-text">
            <input
              type="checkbox"
              bind:checked={removeLeftovers}
              class="mt-0.5 shrink-0"
              aria-describedby="finish-blocks-hint"
            />
            <span>
              <span class="font-medium">{t("tasks.finish_prompt.remove_blocks")}</span>
              <span
                id="finish-blocks-hint"
                class="mt-0.5 block text-[11px] leading-snug text-text-muted"
              >
                {t("tasks.finish_prompt.remove_blocks_hint", {
                  dates: leftoverBlocks.map((b) => fmtDayMonth(b.start)).join(" · "),
                })}
              </span>
            </span>
          </label>
        </div>
        {#if removeLeftovers}
          <input
            type="hidden"
            name="remove_schedule_ids"
            value={leftoverBlocks.map((b) => b.id).join(",")}
          />
        {/if}
      {/if}

      {#if offerLogTime}
        <div class="mt-4 space-y-3 rounded-lg border border-border bg-surface p-3">
          <label class="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" name="log_time" value="1" bind:checked={logTime} />
            {t("tasks.finish_prompt.log_time")}
          </label>
          {#if logTime}
            <input type="hidden" name="log_date" value={logDate} />
            <input type="hidden" name="log_schedule_id" value={logScheduleId} />
            <!-- Two columns rather than a wrapping row: this modal is narrower than the
                 contact-moment form the shape comes from, and the pair belongs side by side —
                 a start above an end reads as two questions instead of one span. -->
            <div class="grid grid-cols-2 items-end gap-3">
              <label class="block min-w-0 text-sm">
                <span class="mb-1 block font-medium text-text">{t("time.field.start")}</span>
                <TimeInput name="log_start" bind:value={logStart} required />
              </label>
              <label class="block min-w-0 text-sm">
                <span class="mb-1 flex items-baseline justify-between gap-2 font-medium text-text">
                  {t("time.field.end")}
                  <span
                    class="text-xs font-semibold tabular-nums {logMinutes
                      ? 'text-brand'
                      : 'text-text-muted'}"
                  >
                    {logMinutes != null
                      ? t("time.worked", { duration: formatMinutes(logMinutes) })
                      : "—"}
                  </span>
                </span>
                <TimeInput name="log_end" bind:value={logEnd} required />
              </label>
            </div>
            <label class="block text-sm">
              <span class="mb-1 block font-medium text-text">{t("time.field.description")}</span>
              <input name="log_description" bind:value={logDescription} class={inputClass} />
            </label>
            <p class="text-xs text-text-muted">
              {logScheduleId
                ? t("tasks.finish_prompt.log_time_hint_block", { date: fmtDayMonth(logDate) })
                : t("tasks.finish_prompt.log_time_hint", { date: fmtDayMonth(logDate) })}
            </p>
          {/if}
        </div>
      {/if}

      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showFinishPrompt = false)}
        >
          {t("tasks.finish_prompt.not_now")}
        </button>
        <Button loading={busy.is("finish")}>
          {t("tasks.finish_prompt.confirm")}
        </Button>
      </div>
    </form>
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
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
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
