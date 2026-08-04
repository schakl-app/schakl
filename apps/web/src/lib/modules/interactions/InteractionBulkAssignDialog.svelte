<script lang="ts">
  /**
   * File a whole selection at once (#299) — the batch form of `InteractionMoveDialog`.
   *
   * It is a separate component rather than that one handed a list of ids, because the two
   * disagree on what an **empty picker means**, and that is the whole contract. The move
   * dialog opens prefilled with one row's links, so clearing a picker there means "clear this
   * link". This one opens **blank over rows that disagree with each other**, so the same
   * gesture must mean "leave every row's own link alone" — otherwise filing a batch by client
   * would wipe the project the Gmail matcher had already worked out on every row the user
   * never opened. Only fields the user actually filled are posted; the action drops the rest,
   * and the API treats absent as "leave alone".
   *
   * Candidates load when the dialog opens, never on page render (docs/PERFORMANCE.md).
   */
  import type { SubmitFunction } from "@sveltejs/kit";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  import { contactsForScope } from "./contacts";
  import { loadLinkLookups, type LinkOption, type ProjectOption, type TaskOption } from "./lookups";

  let {
    ids,
    approvableIds,
    onsaved,
  }: {
    /** The reviewable rows in the selection — re-filable whatever their status. */
    ids: string[];
    /**
     * The pending subset, which is what "file and approve" may act on. It is carried in its
     * own field because the two buttons genuinely act on different sets: a selection may mix
     * rows already reviewed (re-filable, not approvable) with rows still pending.
     */
    approvableIds: string[];
    onsaved?: () => void;
  } = $props();

  let companies = $state<LinkOption[]>([]);
  let projects = $state<ProjectOption[]>([]);
  let tasks = $state<TaskOption[]>([]);
  let contacts = $state<LinkOption[]>([]);
  let loading = $state(true);
  let error = $state("");

  let companyId = $state("");
  let projectId = $state("");
  let taskId = $state("");
  let contactId = $state("");

  /** Nothing picked = nothing to say; the API would answer "0 filed" and look broken. */
  const nothingPicked = $derived(!companyId && !projectId && !taskId && !contactId);

  // The same cascade the move dialog uses: a client narrows the projects, a project the tasks,
  // and picking deeper backfills the levels above.
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

  // The contact roster follows the picked client, like the move dialog's. There is no prefilled
  // contact to preserve here — the dialog starts blank — so a client change simply drops a pick
  // the new client does not know.
  $effect(() => {
    const scope = companyId;
    void (async () => {
      const items = await contactsForScope(scope);
      if (scope !== companyId) return; // the client moved on; this is the wrong roster now
      contacts = items.map((c) => ({
        value: c.id,
        label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
      }));
      if (contactId && !items.some((c) => c.id === contactId)) contactId = "";
    })();
  });

  // --- inline-create behind the task picker (docs/UX.md) ------------------------------------ //
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  let handledCreate = $state("");
  $effect(() => {
    const created = page.form?.inlineCreated as
      | { slot: string; id: string; project_id?: string | null; company_id?: string | null }
      | undefined;
    if (created?.slot !== "bulk_task" || created.id === handledCreate) return;
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

  const busy = new InFlight();
  // File and file-and-approve share the form (#279): key off the clicked button. `reset: false`
  // — the dialog closes on success, and a reset would blank the pickers mid-flight.
  const submit: SubmitFunction = (input) =>
    busy.wrap(input.submitter?.getAttribute("name") === "approve" ? "approve" : "save", () => {
      return async ({ result, update }) => {
        if (result.type === "failure") {
          error = String(result.data?.error ?? "errors.validation");
          return;
        }
        error = "";
        await update({ reset: false });
        onsaved?.();
      };
    })(input);

  $effect(() => {
    void load();
  });

  async function load() {
    loading = true;
    error = "";
    try {
      ({ companies, projects, tasks } = await loadLinkLookups());
    } catch {
      error = "errors.server";
    } finally {
      loading = false;
    }
  }
</script>

<form method="POST" action="?/bulkAssignInteractions" class="space-y-4" use:enhance={submit}>
  <input type="hidden" name="ids" value={ids.join(",")} />
  <!-- "File and approve" acts on the pending subset only; see the prop's note. -->
  <input type="hidden" name="approve_ids" value={approvableIds.join(",")} />

  <p class="text-sm text-text-muted">
    {t("interactions.bulk.assign_hint", { count: ids.length })}
  </p>

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
          placeholder={t("interactions.bulk.unchanged")}
          onselect={(v) => (companyId = v)}
          id="bulk-company"
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.project")}</span>
        <Combobox
          items={projectOptions}
          name="project_id"
          value={projectId}
          placeholder={t("interactions.bulk.unchanged")}
          onselect={onProjectPicked}
          id="bulk-project"
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.task")}</span>
        <Combobox
          items={taskOptions}
          name="task_id"
          value={taskId}
          placeholder={t("interactions.bulk.unchanged")}
          onselect={onTaskPicked}
          oncreate={canCreateTask
            ? (query) => {
                taskDraft = query;
                taskCreateOpen = true;
              }
            : undefined}
          id="bulk-task"
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.contact")}</span>
        <Combobox
          items={contacts}
          name="contact_id"
          value={contactId}
          placeholder={t("interactions.bulk.unchanged")}
          onselect={(v) => (contactId = v)}
          id="bulk-contact"
        />
      </label>
    </div>
  {/if}

  {#if error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}

  <div class="flex justify-end gap-2">
    <Button
      type="submit"
      variant={approvableIds.length > 0 ? "secondary" : "primary"}
      loading={busy.is("save")}
      disabled={loading || nothingPicked || busy.active}
    >
      {t("interactions.bulk.assign_submit")}
    </Button>
    {#if approvableIds.length > 0}
      <!-- File + approve in one step — the batch form of #183. The selection does not survive
           the reload a save triggers, so splitting this into two clicks would lose it. -->
      <Button
        type="submit"
        name="approve"
        value="1"
        formaction="?/bulkApproveInteractions"
        loading={busy.is("approve")}
        disabled={loading || nothingPicked || busy.active}
      >
        {t("interactions.bulk.assign_and_approve", { count: approvableIds.length })}
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
  pickerSlot="bulk_task"
/>
