<script lang="ts">
  /**
   * Log an email nobody's connected mailbox ever saw (#262): pick its `.eml` export, assign it
   * to a client / project / task / contact in the same step, save. The API parses the message —
   * subject, participants, date, body, attachments — so the row reads exactly like a
   * Gmail-synced one; only the bytes came from a file.
   *
   * Rendered inside a `Modal` by whichever surface hosts the timeline, so the affordance exists
   * on the Interacties page and on every company / project / task / contact panel at once. It
   * posts to the **host page's** `?/uploadInteractionEml` action, like every other panel form
   * (docs/UX.md) — the host spreads `interactionActions`, so it already has it.
   *
   * Two things the flow refuses to do quietly: an already-logged message asks before it is
   * logged twice (same `Message-ID`, "toch vastleggen"), and an attachment the storage
   * guardrails refused is reported rather than dropped.
   */
  import { Mail, Paperclip } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  import { loadLinkLookups, type LinkOption, type ProjectOption, type TaskOption } from "./lookups";

  let {
    prefill = {},
    onsaved,
    oncreatecompany,
    oncreateproject,
  }: {
    /** The host entity's link, stamped on the uploaded row (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    onsaved?: () => void;
    /** Inline-create for the unpinned company / project pickers — the host page owns those
     *  dialogs (slots `interaction_company` / `interaction_project`), exactly as for the manual
     *  form. Absent → the picker offers no ＋. */
    oncreatecompany?: (query: string) => void;
    oncreateproject?: (query: string) => void;
  } = $props();

  const busy = new InFlight();

  let filename = $state("");
  let error = $state("");
  let duplicate = $state(false);
  let skipped = $state(0);

  // A dimension the host page already fixed rides along as a hidden input; the rest get a
  // picker, the same split the manual form makes.
  const pinned = (field: string) =>
    typeof prefill[field] === "string" && (prefill[field] as string).length > 0;
  const showCompany = $derived(!pinned("company_id"));
  const showProject = $derived(!pinned("project_id"));
  const showTask = $derived(!pinned("task_id"));
  const hidden = $derived(
    Object.fromEntries(
      Object.entries(prefill).filter(
        ([field, value]) =>
          field !== "contact_id" &&
          !(field === "company_id" && showCompany) &&
          !(field === "project_id" && showProject) &&
          !(field === "task_id" && showTask) &&
          typeof value === "string" &&
          value.length > 0,
      ),
    ),
  );

  // --- link pickers (#183's assign-while-logging, on an upload) ------------------------- //
  let fCompany = $state("");
  let fProject = $state("");
  let fTask = $state("");
  let fContact = $state("");
  let companies = $state<LinkOption[]>([]);
  let projects = $state<ProjectOption[]>([]);
  let tasks = $state<TaskOption[]>([]);
  let contacts = $state<LinkOption[]>([]);

  // Candidates load when the dialog opens, never on page render (docs/PERFORMANCE.md).
  $effect(() => {
    void loadLinkLookups({
      companyId: pinned("company_id") ? (prefill.company_id as string) : null,
      projectId: pinned("project_id") ? (prefill.project_id as string) : null,
    }).then((lookups) => {
      companies = lookups.companies;
      projects = lookups.projects;
      tasks = lookups.tasks;
    });
  });
  $effect(() => {
    const scope = pinned("company_id") ? `&company_id=${prefill.company_id as string}` : "";
    void (async () => {
      const response = await fetch(`/api/v1/contacts?limit=200${scope}`, {
        headers: { accept: "application/json" },
      });
      const items: { id: string; first_name: string; last_name?: string | null }[] = response.ok
        ? ((await response.json()).items ?? [])
        : [];
      contacts = items.map((c) => ({
        value: c.id,
        label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
      }));
    })();
  });

  // The move dialog's cascade: a client narrows projects, a project narrows tasks, and picking
  // deeper backfills the levels above.
  const effCompany = $derived(
    fCompany || (typeof prefill.company_id === "string" ? prefill.company_id : ""),
  );
  const effProject = $derived(
    fProject || (typeof prefill.project_id === "string" ? prefill.project_id : ""),
  );
  const projectOptions = $derived(
    effCompany ? projects.filter((p) => !p.company_id || p.company_id === effCompany) : projects,
  );
  const taskOptions = $derived(
    effProject
      ? tasks.filter((task) => task.project_id === effProject)
      : effCompany
        ? tasks.filter((task) => !task.company_id || task.company_id === effCompany)
        : tasks,
  );
  function onProjectPicked(id: string) {
    fProject = id;
    const project = projects.find((p) => p.value === id);
    if (project?.company_id && showCompany) fCompany = project.company_id;
    if (fTask && tasks.find((task) => task.value === fTask)?.project_id !== id) fTask = "";
  }
  function onTaskPicked(id: string) {
    fTask = id;
    const task = tasks.find((option) => option.value === id);
    if (task?.project_id) onProjectPicked(task.project_id);
  }

  // --- inline-create behind the pickers (docs/UX.md) ------------------------------------- //
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  let qcOpen = $state(false);
  let qcName = $state("");
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);
  async function quickCreateContact(query: string) {
    qcName = query;
    if (contactDefinitions === null) {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=contact", {
        headers: { accept: "application/json" },
      });
      contactDefinitions = response.ok ? await response.json() : [];
    }
    qcOpen = true;
  }
  let companyQuery = $state("");
  let projectQuery = $state("");
  let handledCreate = $state("");
  $effect(() => {
    const created = page.form?.inlineCreated as
      | {
          slot: string;
          id: string;
          name?: string;
          project_id?: string | null;
          company_id?: string | null;
        }
      | undefined;
    if (!created || created.id === handledCreate) return;
    if (created.slot === "eml_contact") {
      handledCreate = created.id;
      if (!contacts.some((c) => c.value === created.id)) {
        contacts = [...contacts, { value: created.id, label: qcName || "—" }];
      }
      fContact = created.id;
    } else if (created.slot === "eml_task") {
      handledCreate = created.id;
      if (!tasks.some((task) => task.value === created.id)) {
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
    } else if (created.slot === "interaction_company") {
      handledCreate = created.id;
      if (!companies.some((c) => c.value === created.id)) {
        companies = [...companies, { value: created.id, label: companyQuery || "—" }];
      }
      fCompany = created.id;
    } else if (created.slot === "interaction_project") {
      handledCreate = created.id;
      if (!projects.some((p) => p.value === created.id)) {
        projects = [
          ...projects,
          {
            value: created.id,
            label: created.name ?? (projectQuery || "—"),
            company_id: created.company_id ?? null,
          },
        ];
      }
      onProjectPicked(created.id);
    }
  });
</script>

<form
  method="POST"
  action="?/uploadInteractionEml"
  enctype="multipart/form-data"
  class="space-y-4"
  use:enhance={busy.wrap("", () => async ({ result, update }) => {
    if (result.type === "failure") {
      duplicate = Boolean(result.data?.emlDuplicate);
      error = String(result.data?.error ?? "errors.validation");
      return;
    }
    error = "";
    duplicate = false;
    const uploaded = (result.type === "success" ? result.data?.emlUploaded : null) as
      { stored: number; skipped: number } | null | undefined;
    skipped = uploaded?.skipped ?? 0;
    await update({ reset: false });
    // A skipped attachment is worth a sentence, so the modal stays open to say it.
    if (!skipped) onsaved?.();
  })}
>
  {#each Object.entries(hidden) as [field, value] (field)}
    <input type="hidden" name={field} {value} />
  {/each}
  <!-- Set only after the duplicate warning: the second press is the deliberate one. -->
  <input type="hidden" name="allow_duplicate" value={duplicate ? "1" : "0"} />

  <div>
    <span class="mb-1 block text-sm font-medium text-text">{t("interactions.eml.file")}</span>
    <label
      class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm text-text-muted hover:border-brand hover:text-brand"
    >
      <Paperclip size={15} aria-hidden="true" />
      {filename || t("interactions.eml.choose")}
      <input
        type="file"
        name="file"
        accept=".eml,message/rfc822"
        required
        class="hidden"
        onchange={(e) => {
          filename = e.currentTarget.files?.[0]?.name ?? "";
          duplicate = false;
          error = "";
          skipped = 0;
        }}
      />
    </label>
    <p class="mt-1 text-xs text-text-muted">{t("interactions.eml.hint")}</p>
  </div>

  <div class="grid gap-4 sm:grid-cols-2">
    {#if showCompany}
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.company")}</span>
        <Combobox
          items={companies}
          name="company_id"
          value={fCompany}
          placeholder={t("common.none")}
          onselect={(v) => (fCompany = v)}
          oncreate={oncreatecompany
            ? (query) => {
                companyQuery = query;
                oncreatecompany?.(query);
              }
            : undefined}
          id="eml-company"
        />
      </label>
    {/if}
    {#if showProject}
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.project")}</span>
        <Combobox
          items={projectOptions}
          name="project_id"
          value={fProject}
          placeholder={t("common.none")}
          onselect={onProjectPicked}
          oncreate={oncreateproject
            ? (query) => {
                projectQuery = query;
                oncreateproject?.(query);
              }
            : undefined}
          id="eml-project"
        />
      </label>
    {/if}
    {#if showTask}
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.task")}</span>
        <Combobox
          items={taskOptions}
          name="task_id"
          value={fTask}
          placeholder={t("common.none")}
          onselect={onTaskPicked}
          oncreate={canCreateTask
            ? (query) => {
                taskDraft = query;
                taskCreateOpen = true;
              }
            : undefined}
          id="eml-task"
        />
      </label>
    {/if}
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.contact")}</span>
      <Combobox
        items={contacts}
        name="contact_id"
        value={fContact}
        placeholder={t("interactions.field.contact_placeholder")}
        onselect={(v) => (fContact = v)}
        oncreate={(query) => void quickCreateContact(query)}
        id="eml-contact"
      />
    </label>
  </div>

  {#if skipped}
    <p class="text-sm text-amber-700 dark:text-amber-400">
      {t("interactions.eml.attachments_skipped", { count: skipped })}
    </p>
  {/if}
  {#if duplicate}
    <p class="text-sm text-amber-700 dark:text-amber-400">{t("interactions.eml.duplicate")}</p>
  {:else if error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}

  <div class="flex justify-end gap-2">
    {#if skipped}
      <Button type="button" variant="secondary" onclick={() => onsaved?.()}>
        {t("common.close")}
      </Button>
    {/if}
    <Button type="submit" loading={busy.active}>
      <Mail size={15} aria-hidden="true" />
      {duplicate ? t("interactions.eml.upload_anyway") : t("interactions.eml.submit")}
    </Button>
  </div>
</form>

<ContactQuickCreate
  bind:open={qcOpen}
  name={qcName}
  definitions={contactDefinitions ?? []}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionContact"
  pickerSlot="eml_contact"
  error={(page.form?.qcError as string | undefined) ?? null}
/>

<TaskQuickCreate
  bind:open={taskCreateOpen}
  title={taskDraft}
  companyId={effCompany || null}
  projectId={effProject || null}
  members={(page.data.members as
    { user_id: string; full_name: string | null; email: string }[] | undefined) ?? []}
  action="?/createInteractionTask"
  error={(page.form?.qcError as string | undefined) ?? null}
  pickerSlot="eml_task"
/>
