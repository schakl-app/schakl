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
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

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
    projectId ? tasks.filter((task) => task.project_id === projectId) : tasks,
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
        get("/api/v1/companies?limit=200&count=false"),
        get("/api/v1/projects?limit=200&count=false"),
        get("/api/v1/tasks?limit=200&count=false&meta=false"),
        get("/api/v1/contacts?limit=200"),
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
        (task: { id: string; title: string; project_id?: string | null }) => ({
          value: task.id,
          label: task.title,
          project_id: task.project_id ?? null,
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

<form
  method="POST"
  action="?/moveInteraction"
  class="space-y-4"
  use:enhance={() =>
    async ({ result, update }) => {
      if (result.type === "failure") {
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      await update({ reset: false });
      onsaved?.();
    }}
>
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
  {/if}

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end gap-2">
    <button
      type="submit"
      disabled={loading}
      class="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 {canApprove
        ? 'border border-border text-text hover:bg-surface'
        : 'bg-brand text-white hover:opacity-90'}"
    >
      {canApprove ? t("interactions.save_pending") : t("common.save")}
    </button>
    {#if canApprove}
      <!-- Link + approve in one step (#183); `assign=1` tells the action to carry the links. -->
      <button
        type="submit"
        name="assign"
        value="1"
        formaction={approveAction}
        disabled={loading}
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
      >
        {t("interactions.approve")}
      </button>
    {/if}
  </div>
</form>
