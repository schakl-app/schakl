<script lang="ts">
  /**
   * Move / re-link a contactmoment (#147): the four link pickers, prefilled with the row's
   * current links, posting to the host page's `?/moveInteraction` action (a panel edits
   * through its host, docs/UX.md). The API keeps deriving a missing client from a picked
   * task/project, so clearing the client while picking a task still lands somewhere sane.
   *
   * Candidates load once, when the dialog opens — never on page load (docs/PERFORMANCE.md):
   * a rarely opened dialog must not tax every detail-page render with four lookups.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { SubmitFunction } from "@sveltejs/kit";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  import type { InteractionItem } from "./format";

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
  }
  interface ProjectOption extends Option {
    company_id: string | null;
  }

  let companies = $state<Option[]>([]);
  let projects = $state<ProjectOption[]>([]);
  let tasks = $state<TaskOption[]>([]);
  let contacts = $state<Option[]>([]);
  let loading = $state(true);
  let error = $state("");

  let companyId = $state(interaction.company_id ?? "");
  let projectId = $state(interaction.project_id ?? "");
  let taskId = $state(interaction.task_id ?? "");
  let contactId = $state(interaction.contact_id ?? "");

  // Picks cascade the way the tasks page's filters do: a client narrows the projects, a
  // project narrows the tasks — and picking deeper backfills the levels above.
  const projectOptions = $derived(
    companyId ? projects.filter((p) => !p.company_id || p.company_id === companyId) : projects,
  );
  const taskOptions = $derived(
    projectId
      ? tasks.filter((task) => task.project_id === projectId)
      : companyId
        ? tasks.filter((task) => !task.company_id || task.company_id === companyId)
        : tasks,
  );

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
  // the guard mirrors the API (a close is a task write), which stays the boundary.
  const canCloseTask = $derived(canApprove && can(page.data.user, "tasks.task.write"));
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

  // --- inline-create behind the task picker (docs/UX.md) ------------------------------------ //
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  let handledCreate = $state("");
  $effect(() => {
    const created = page.form?.inlineCreated as
      | { slot: string; id: string; project_id?: string | null; company_id?: string | null }
      | undefined;
    if (created?.slot !== "move_task" || created.id === handledCreate) return;
    handledCreate = created.id;
    if (!tasks.some((option) => option.value === created.id)) {
      tasks = [
        ...tasks,
        {
          value: created.id,
          label: taskDraft || "—",
          project_id: created.project_id ?? null,
          company_id: created.company_id ?? null,
        },
      ];
    }
    onTaskPicked(created.id);
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
          await update({ reset: false });
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
      // Lean lookups: no counts, no task aggregates (docs/PERFORMANCE.md).
      const [companiesPage, projectsPage, tasksPage, contactsPage] = await Promise.all([
        get("/api/v1/companies?limit=200&count=false&sort=name"),
        get("/api/v1/projects?limit=200&count=false"),
        get("/api/v1/tasks?limit=200&count=false&meta=false&sort=title"),
        get("/api/v1/contacts?limit=200&sort=first_name"),
      ]);
      companies = (companiesPage.items ?? []).map((c: { id: string; name: string }) => ({
        value: c.id,
        label: c.name,
      }));
      projects = (projectsPage.items ?? []).map(
        (p: { id: string; name: string; company_id?: string | null }) => ({
          value: p.id,
          label: p.name,
          company_id: p.company_id ?? null,
        }),
      );
      tasks = (tasksPage.items ?? []).map(
        (task: {
          id: string;
          title: string;
          project_id?: string | null;
          company_id?: string | null;
        }) => ({
          value: task.id,
          label: task.title,
          project_id: task.project_id ?? null,
          company_id: task.company_id ?? null,
        }),
      );
      contacts = (contactsPage.items ?? []).map(
        (c: { id: string; first_name: string; last_name?: string | null }) => ({
          value: c.id,
          label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
        }),
      );
    } catch {
      error = "errors.server";
    } finally {
      loading = false;
    }
  }
</script>

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
          items={companies}
          name="company_id"
          value={companyId}
          placeholder={t("common.none")}
          onselect={(v) => (companyId = v)}
          id="move-company"
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.project")}</span>
        <Combobox
          items={projectOptions}
          name="project_id"
          value={projectId}
          placeholder={t("common.none")}
          onselect={onProjectPicked}
          id="move-project"
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.task")}</span>
        <Combobox
          items={taskOptions}
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
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.contact")}</span>
        <Combobox
          items={contacts}
          name="contact_id"
          value={contactId}
          placeholder={t("common.none")}
          onselect={(v) => (contactId = v)}
          id="move-contact"
        />
      </label>
    </div>

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
      <!-- Link + approve in one step (#183); `assign=1` tells the action to carry the links. -->
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
