<script lang="ts">
  /**
   * Move / re-link a contactmoment (#147): the four link pickers, prefilled with the row's
   * current links, posting to the host page's `?/moveInteraction` action (a panel edits
   * through its host, docs/UX.md). The API keeps deriving a missing client from a picked
   * task/project, so clearing the client while picking a task still lands somewhere sane.
   *
   * Candidates load once, when the dialog opens — never on page load (docs/PERFORMANCE.md):
   * a rarely opened dialog must not tax every detail-page render with four lookups.
   *
   * All four pickers create what they cannot find (docs/UX.md — per-picker definition of done):
   * reviewing a mail that turns out to *be* new work, or to come from a client nobody has
   * entered yet, is the moment that record exists, so the ＋ opens the full create dialog here
   * and the approve carries the row it made. Self-contained — the actions ride in
   * `interactionActions`, so every host already has them.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { SubmitFunction } from "@sveltejs/kit";
  import { aiEnabled } from "$lib/core/ai";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import ProjectQuickCreate from "$lib/modules/projects/ProjectQuickCreate.svelte";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";
  import TaskReviewDialog from "$lib/modules/tasks/TaskReviewDialog.svelte";
  import { fmtDateTime } from "$lib/core/format";
  import { companyArchivedLabel } from "$lib/modules/companies/picker";
  import { projectArchivedLabel } from "$lib/modules/projects/picker";

  import ContactChips from "./ContactChips.svelte";
  import { splitLinkOptions } from "./lookups";
  import type { InteractionItem } from "./format";
  import { ContactRoster, initialContacts } from "./roster.svelte";

  let {
    interaction,
    onsaved,
    approveAction = null,
  }: {
    interaction: InteractionItem;
    onsaved?: () => void;
    /** When set on a pending gmail row (#183), a second "Goedkeuren" button that assigns
     *  these same links and approves in one step; the plain save just re-links. */
    approveAction?: string | null;
  } = $props();

  // Assigning-while-approving only applies to a pending gmail row the owner is reviewing.
  const canApprove = $derived(
    Boolean(approveAction) && interaction.status === "pending" && interaction.source === "gmail",
  );

  interface Option {
    value: string;
    label: string;
  }
  interface TaskOption extends Option {
    project_id: string | null;
    company_id: string | null;
    /**
     * Whose task it is — "sluit deze taak" is a task write, and `:own` means assignee. The
     * whole roster, because `:own` is satisfied by *any* of them (`caller_may_write_task`),
     * so a task shared by two people offers the close to both.
     */
    assignees: { user_id: string }[];
    assignee_user_id: string | null;
  }
  interface ProjectOption extends Option {
    company_id: string | null;
  }

  let companies = $state<Option[]>([]);
  let projects = $state<ProjectOption[]>([]);
  let tasks = $state<TaskOption[]>([]);
  let loading = $state(true);
  let error = $state("");

  let companyId = $state(interaction.company_id ?? "");
  let projectId = $state(interaction.project_id ?? "");
  let taskId = $state(interaction.task_id ?? "");
  // svelte-ignore state_referenced_locally — the dialog is keyed per row; props never swap here.
  const roster = new ContactRoster(initialContacts(interaction));

  // Picks cascade the way the tasks page's filters do: a client narrows the projects, a
  // project narrows the tasks — and picking deeper backfills the levels above.
  // The cascade decides *which* rows may be offered; the split decides which of them are
  // suggested. An archived client, a finished project and a closed task each drop behind the
  // search wearing their status rather than out of the picker (`lookups.splitLinkOptions`).
  const linkSplit = $derived(
    splitLinkOptions(
      {
        companies,
        projects: companyId
          ? projects.filter((p) => !p.company_id || p.company_id === companyId)
          : projects,
        tasks: projectId
          ? tasks.filter((task) => task.project_id === projectId)
          : companyId
            ? tasks.filter((task) => !task.company_id || task.company_id === companyId)
            : tasks,
      },
      { companyId: companyId, projectId: projectId, taskId: taskId },
    ),
  );
  const projectOptions = $derived(linkSplit.projects.live);
  const taskOptions = $derived(linkSplit.tasks.live);

  function onProjectPicked(id: string) {
    projectId = id;
    const project = projects.find((p) => p.value === id);
    if (project?.company_id) companyId = project.company_id;
    if (taskId && tasks.find((task) => task.value === taskId)?.project_id !== id) taskId = "";
  }

  function onTaskPicked(id: string) {
    taskId = id;
    const task = tasks.find((option) => option.value === id);
    if (task?.project_id) onProjectPicked(task.project_id);
  }

  /**
   * The contact roster is part of the same cascade — a client narrows it to that client's own
   * people. It used to be loaded once, unscoped, alongside the other three lookups, so re-filing
   * a moment onto client B still offered client A's contacts (and everyone else's) and the
   * dialog would happily save the mismatch. `ContactRoster` owns that rule now, and the create
   * form and the .eml upload obey the same copy of it (#300).
   */
  $effect(() => {
    void roster.load(companyId);
  });

  // --- close the task with this contact moment, while approving (#157 in the review) ------- //
  interface StatusDef {
    id: string;
    key: string;
    name: string;
    is_terminal: boolean;
  }
  let closeTask = $state(false);
  let terminal = $state<StatusDef[]>([]);
  let terminalLoaded = $state(false);
  let closeStatus = $state("");
  // Offered whenever a task is picked — whether or not that task *requires* a closing moment;
  // the guard mirrors the API (a close is a task write), which stays the boundary. Per picked
  // task, because `tasks.task.write:own` means assignee: the base key put the box on every
  // colleague's task and the close was refused.
  const canCloseTask = $derived(
    canApprove && canWriteTask(page.data.user, tasks.find((task) => task.value === taskId) ?? null),
  );
  // "Laat schakl deze taak invullen" (#327): the same per-task write gate the close box uses —
  // filling a task in is a task write — plus the AI gate, which is what keeps the tick off the
  // screen entirely for an org with no provider configured ("off means invisible", #126).
  const canEnrichTask = $derived(canCloseTask && aiEnabled(page.data.user, "email_assist"));
  let enrichTask = $state(false);
  // Terminal statuses load when the box is first ticked — never on page load (PERFORMANCE.md).
  $effect(() => {
    if (closeTask && !terminalLoaded) {
      terminalLoaded = true;
      void loadTerminal();
    }
  });
  async function loadTerminal() {
    try {
      const response = await fetch("/api/v1/tasks/statuses", {
        headers: { accept: "application/json" },
      });
      const statuses: StatusDef[] = response.ok ? await response.json() : [];
      terminal = statuses.filter((status) => status.is_terminal);
      closeStatus = terminal[0]?.key ?? "";
    } catch {
      terminal = [];
    }
  }

  // --- inline-create behind all four pickers (docs/UX.md) ----------------------------------- //
  // The gates are the API's own keys, not `!isPortal` (§15): a control that would 403 is never
  // drawn.
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  // Projects have no separate create permission — writing one is creating one.
  const canCreateProject = $derived(can(page.data.user, "projects.project.write"));
  const canCreateCompany = $derived(can(page.data.user, "companies.company.write"));
  const canCreateContact = $derived(can(page.data.user, "contacts.contact.write"));
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  let projectCreateOpen = $state(false);
  let projectDraft = $state("");
  let companyCreateOpen = $state(false);
  let companyDraft = $state("");
  let contactCreateOpen = $state(false);
  let contactDraft = $state("");
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);
  /**
   * The new contact is offered a link to the moment's client (#247), and the box needs the
   * client's *name*: the roster this dialog loaded knows it whenever one is picked, so the ＋
   * costs no lookup. A moment with no client yet simply gets an unlinked contact.
   */
  const contactLinkCompany = $derived.by(() => {
    const label = companies.find((c) => c.value === companyId)?.label;
    return companyId && label ? { id: companyId, name: label } : null;
  });
  async function startContactCreate(query: string) {
    contactDraft = query;
    // Fetched on first open, never on page load (docs/PERFORMANCE.md); creating is held until
    // they land, so a required field the form never drew can't come back as a validation error.
    if (contactDefinitions === null) {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=contact", {
        headers: { accept: "application/json" },
      });
      contactDefinitions = response.ok ? await response.json() : [];
    }
    contactCreateOpen = true;
  }
  /**
   * A form result outlives the dialog that produced it, and this dialog is keyed per row: create
   * a project while reviewing one email, close without approving, open the next one, and the
   * fresh instance would read the *previous* `inlineCreated` and quietly file this message onto
   * that project — pre-filled, plausible, and wrong. An id already on `page.form` at mount was
   * therefore answered by somebody else, so it starts out acknowledged. Deliberate initial
   * capture: only a create made *by this instance* arrives after mount.
   */
  let handledCreate = $state((page.form?.inlineCreated as { id?: string } | undefined)?.id ?? "");
  /**
   * The task this dialog made, if it made one.
   *
   * Approving an email that turned out to *be* new work leaves a task holding a title, a client
   * and nothing else — and, when "laat schakl deze taak invullen" is ticked, a worker about to
   * write its notes, its checklist and its deadline. The reviewer's next act is checking that
   * task either way, so the approve opens it **here**, in a review slide-over beside the message
   * it came from (`TaskReviewDialog`), rather than closing over the inbox and asking them to
   * find it — or, as it did before, redirecting them onto the task's page and losing the inbox
   * and the e-mail both (docs/UX.md Principle 8: a dialog is the default).
   *
   * Only a task **created here** does that. Filing a message onto a task that already exists is
   * the ordinary case and must stay where it is: the reviewer is working an inbox, and a review
   * per approve would be a dialog over every row they file.
   */
  let taskCreatedHere = $state("");
  /** The task under review after the approve — while set, the host is not told to close. */
  let reviewTaskId = $state("");
  let reviewOpen = $state(false);
  const reviewOrigin = $derived.by(() => {
    const from =
      interaction.participants?.find((p) => p.role === "from") ?? interaction.participants?.[0];
    const who = from?.name || from?.email || interaction.contact_name || "";
    return {
      label: t("tasks.review.origin"),
      title: interaction.subject || interaction.task_title || t("interactions.detail_title"),
      detail: [who, fmtDateTime(interaction.occurred_at)].filter(Boolean).join(" · "),
    };
  });
  $effect(() => {
    const created = page.form?.inlineCreated as
      | {
          slot: string;
          id: string;
          name?: string;
          project_id?: string | null;
          company_id?: string | null;
          assignees?: { user_id: string }[] | null;
          assignee_user_id?: string | null;
        }
      | undefined;
    if (!created || created.id === handledCreate) return;
    if (created.slot === "move_task") {
      handledCreate = created.id;
      // Made here, on this message: the approve that follows opens it in edit mode (below).
      taskCreatedHere = created.id;
      if (!tasks.some((option) => option.value === created.id)) {
        tasks = [
          ...tasks,
          {
            value: created.id,
            label: created.name ?? (taskDraft || "—"),
            project_id: created.project_id ?? null,
            company_id: created.company_id ?? null,
            assignees: created.assignees ?? [],
            assignee_user_id: created.assignee_user_id ?? null,
          },
        ];
      }
      onTaskPicked(created.id);
    } else if (created.slot === "move_project") {
      handledCreate = created.id;
      if (!projects.some((option) => option.value === created.id)) {
        projects = [
          ...projects,
          {
            value: created.id,
            label: created.name ?? (projectDraft || "—"),
            company_id: created.company_id ?? null,
          },
        ];
      }
      // The picker's own cascade, so a project created under a client backfills the client —
      // and the approve that follows carries both.
      onProjectPicked(created.id);
    } else if (created.slot === "move_company") {
      handledCreate = created.id;
      if (!companies.some((option) => option.value === created.id)) {
        companies = [
          ...companies,
          { value: created.id, label: created.name ?? (companyDraft || "—") },
        ];
      }
      companyId = created.id;
    } else if (created.slot === "move_contact") {
      handledCreate = created.id;
      // Offers them, adds the chip, and drops the shared per-scope cache so the next form to
      // open knows about them too (#290).
      roster.created(created.id, created.name ?? (contactDraft || "—"));
    }
  });

  // Approve succeeded but the close PATCH bounced (e.g. a status policy): say exactly that —
  // a plain error here would read as "the approve failed", which it did not.
  let closeFailedAfterApprove = $state(false);

  const busy = new InFlight();
  // Save and approve share the form (#279): key off the clicked button.
  const submit: SubmitFunction = (input) =>
    busy.wrap(
      input.submitter?.getAttribute("name") === "assign" ? "approve" : "save",
      () =>
        async ({ result, update }) => {
          if (result.type === "failure") {
            closeFailedAfterApprove = Boolean(result.data?.approvedButCloseFailed);
            error = String(result.data?.error ?? "errors.validation");
            return;
          }
          error = "";
          closeFailedAfterApprove = false;
          const review =
            result.type === "success" ? (result.data?.reviewTaskId as string | undefined) : "";
          await update({ reset: false });
          if (review) {
            // The approve that made a task hands it back: the review opens over this dialog and
            // the host is told to close only when that review is done (`onReviewClosed`).
            reviewTaskId = review;
            reviewOpen = true;
            return;
          }
          onsaved?.();
        },
    )(input);

  $effect(() => {
    void loadCandidates();
  });

  async function loadCandidates() {
    loading = true;
    error = "";
    try {
      const get = async (url: string) => {
        const response = await fetch(url, { headers: { accept: "application/json" } });
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      };
      // Lean lookups: no counts, no task aggregates (docs/PERFORMANCE.md). Contacts come from
      // the shared per-scope cache instead of a fourth hand-rolled fetch, so this dialog and
      // the create form on the same page narrow to the same client and share the flight.
      const [companiesPage, projectsPage, tasksPage] = await Promise.all([
        get("/api/v1/companies?limit=200&count=false&sort=name"),
        get("/api/v1/projects?limit=200&count=false"),
        get("/api/v1/tasks?limit=200&count=false&meta=false&sort=title"),
      ]);
      companies = (companiesPage.items ?? []).map(
        (c: { id: string; name: string; status?: string | null }) => ({
          value: c.id,
          label: c.name,
          status: c.status ?? null,
        }),
      );
      projects = (projectsPage.items ?? []).map(
        (p: { id: string; name: string; company_id?: string | null; status?: string | null }) => ({
          value: p.id,
          label: p.name,
          company_id: p.company_id ?? null,
          status: p.status ?? null,
        }),
      );
      tasks = (tasksPage.items ?? []).map(
        (task: {
          id: string;
          title: string;
          project_id?: string | null;
          company_id?: string | null;
          assignees?: { user_id: string }[] | null;
          assignee_user_id?: string | null;
          completed_at?: string | null;
        }) => ({
          value: task.id,
          label: task.title,
          project_id: task.project_id ?? null,
          company_id: task.company_id ?? null,
          assignees: (task.assignees ?? []).map((entry) => ({ user_id: entry.user_id })),
          assignee_user_id: task.assignee_user_id ?? null,
          completed_at: task.completed_at ?? null,
        }),
      );
    } catch {
      error = "errors.server";
    } finally {
      loading = false;
    }
  }
</script>

{#if reviewTaskId}
  <!-- Approved, and the task it made is open beside this for review. The form is done; what
       is left of this dialog says so, and closes with the review. -->
  <p class="text-sm text-text-muted">{t("interactions.approved_review")}</p>
{:else}
  <form method="POST" action="?/moveInteraction" class="space-y-4" use:enhance={submit}>
    <input type="hidden" name="id" value={interaction.id} />
    <input type="hidden" name="source" value={interaction.source} />

    {#if loading}
      <p class="text-sm text-text-muted">{t("common.loading")}</p>
    {:else}
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.company")}</span>
          <Combobox
            items={linkSplit.companies.live}
            archived={linkSplit.companies.retired}
            archivedLabel={companyArchivedLabel()}
            name="company_id"
            value={companyId}
            placeholder={t("common.none")}
            onselect={(v) => (companyId = v)}
            oncreate={canCreateCompany
              ? (query) => {
                  companyDraft = query;
                  companyCreateOpen = true;
                }
              : undefined}
            id="move-company"
          />
        </label>
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.project")}</span>
          <Combobox
            items={projectOptions}
            archived={linkSplit.projects.retired}
            archivedLabel={projectArchivedLabel()}
            name="project_id"
            value={projectId}
            placeholder={t("common.none")}
            onselect={onProjectPicked}
            oncreate={canCreateProject
              ? (query) => {
                  projectDraft = query;
                  projectCreateOpen = true;
                }
              : undefined}
            id="move-project"
          />
        </label>
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.task")}</span>
          <Combobox
            items={taskOptions}
            archived={linkSplit.tasks.retired}
            archivedLabel={t("tasks.picker.archived")}
            name="task_id"
            value={taskId}
            placeholder={t("common.none")}
            onselect={onTaskPicked}
            oncreate={canCreateTask
              ? (query) => {
                  taskDraft = query;
                  taskCreateOpen = true;
                }
              : undefined}
            id="move-task"
          />
        </label>
        <div class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.contacts")}</span>
          <ContactChips
            {roster}
            id="move-contacts"
            oncreate={canCreateContact ? (query) => void startContactCreate(query) : undefined}
          />
        </div>
      </div>

      {#if canEnrichTask && taskId}
        <!-- Carry the email into the task while approving (#327) — the opening move to
           "sluit deze taak hiermee"'s closing one, and it reads like its sibling. Off by
           default: sending a client's own words to a model is a decision, not an inheritance. -->
        <label class="flex items-start gap-2 rounded-lg border border-border p-3 text-sm text-text">
          <input
            type="checkbox"
            name="enrich_task"
            value="1"
            bind:checked={enrichTask}
            class="mt-0.5"
          />
          <span>
            {t("interactions.approve_enrich_task")}
            <span class="mt-0.5 block text-xs text-text-muted"
              >{t("interactions.approve_enrich_task_hint")}</span
            >
          </span>
        </label>
      {/if}

      {#if canCloseTask && taskId}
        <!-- Close the task with this contact moment while approving (#157): offered for any
           picked task, required-close or not; the status pick mirrors CloseTaskDialog. -->
        <div class="space-y-2 rounded-lg border border-border p-3">
          <label class="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" name="close_task" value="1" bind:checked={closeTask} />
            {t("interactions.approve_close_task")}
          </label>
          {#if closeTask}
            {#if terminalLoaded && terminal.length === 0}
              <p class="text-sm text-red-600">{t("interactions.close_task_no_terminal")}</p>
            {:else if terminal.length > 1}
              <fieldset class="space-y-1.5 pl-6">
                <legend class="sr-only">{t("interactions.close_task_pick_status")}</legend>
                {#each terminal as status (status.id)}
                  <label class="flex items-center gap-2 text-sm text-text">
                    <input
                      type="radio"
                      name="close_status"
                      value={status.key}
                      bind:group={closeStatus}
                    />
                    {status.name}
                  </label>
                {/each}
              </fieldset>
            {:else if terminal.length === 1}
              <input type="hidden" name="close_status" value={closeStatus} />
            {/if}
          {/if}
        </div>
      {/if}
    {/if}

    {#if closeFailedAfterApprove}
      <p class="text-sm text-red-600">{t("interactions.close_after_approve_failed")}</p>
    {/if}
    {#if error}
      <p class="text-sm text-red-600">{t(error)}</p>
    {/if}

    <div class="flex justify-end gap-2">
      <Button
        type="submit"
        variant={canApprove ? "secondary" : "primary"}
        loading={busy.is("save")}
        disabled={loading || busy.active}
      >
        {canApprove ? t("interactions.save_pending") : t("common.save")}
      </Button>
      {#if canApprove}
        <!-- Link + approve in one step (#183); `assign=1` tells the action to carry the links.
           `review_task` rides with it when the task on this message was created in this dialog:
           a task that only exists because of this email is unfinished by definition, so the
           approve hands it back and it opens for review beside the message. -->
        {#if taskId && taskId === taskCreatedHere}
          <input type="hidden" name="review_task" value="1" />
        {/if}
        <Button
          type="submit"
          name="assign"
          value="1"
          formaction={approveAction}
          loading={busy.is("approve")}
          disabled={loading || busy.active}
        >
          {t("interactions.approve")}
        </Button>
      {/if}
    </div>
  </form>
{/if}

<!-- The task the approve just made, open for review beside the message (see `taskCreatedHere`).
     The project options are this dialog's own, already narrowed to the client the task was
     filed under; every way out of the review closes the host with it. -->
{#if reviewTaskId}
  <TaskReviewDialog
    bind:open={reviewOpen}
    taskId={reviewTaskId}
    origin={reviewOrigin}
    projects={projectOptions}
    archivedProjects={linkSplit.projects.retired}
    members={(page.data.members as
      { user_id: string; full_name: string | null; email: string }[] | undefined) ?? []}
    action="?/updateReviewTask"
    onclose={() => onsaved?.()}
  />
{/if}

<!-- The client roster is the one this dialog already loaded, so the ＋ costs no second fetch. -->
<ProjectQuickCreate
  bind:open={projectCreateOpen}
  name={projectDraft}
  {companies}
  {companyId}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionProject"
  error={(page.form?.qcError as string | undefined) ?? null}
  pickerSlot="move_project"
/>

<TaskQuickCreate
  bind:open={taskCreateOpen}
  title={taskDraft}
  companyId={companyId || null}
  projectId={projectId || null}
  members={(page.data.members as
    { user_id: string; full_name: string | null; email: string }[] | undefined) ?? []}
  action="?/createInteractionTask"
  error={(page.form?.qcError as string | undefined) ?? null}
  pickerSlot="move_task"
/>

<CompanyQuickCreate
  bind:open={companyCreateOpen}
  name={companyDraft}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionCompany"
  pickerSlot="move_company"
  error={(page.form?.qcError as string | undefined) ?? null}
/>

<!-- The new person is offered a link to the client this moment is being filed to (#247). -->
<ContactQuickCreate
  bind:open={contactCreateOpen}
  name={contactDraft}
  linkCompany={contactLinkCompany}
  definitions={contactDefinitions ?? []}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionContact"
  pickerSlot="move_contact"
  error={(page.form?.qcError as string | undefined) ?? null}
/>
