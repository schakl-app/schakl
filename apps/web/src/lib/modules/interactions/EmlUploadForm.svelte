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
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import ProjectQuickCreate from "$lib/modules/projects/ProjectQuickCreate.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  import { contactsForScope, forgetContacts } from "./contacts";
  import { loadLinkLookups, type LinkOption, type ProjectOption, type TaskOption } from "./lookups";

  let {
    prefill = {},
    onsaved,
  }: {
    /** The host entity's link, stamped on the uploaded row (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    onsaved?: () => void;
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

  /**
   * The contact roster follows the upload's **effective** client, exactly as the manual form's
   * does — the host's pinned client, the one picked below, or the one backfilled from a project
   * or task pick. It used to read only the pinned one, so the picker on the Interacties page
   * listed every contact in the org however the message was being filed, and a client change
   * never narrowed it. Same shared, per-scope cache the manual form uses, so the two modals on
   * one page share a flight instead of racing (docs/PERFORMANCE.md).
   *
   * A client the user changed drops a pick the new client does not know, the way the cascade
   * above drops a task; `loadedScope` is `null` only before the first roster lands, so nothing
   * is dropped merely because the dialog opened.
   */
  let loadedScope: string | null = null;
  let contactCleared = $state(false);
  $effect(() => {
    const scope = effCompany;
    void (async () => {
      const items = await contactsForScope(scope);
      // The client moved on while this flight was out — its answer is already the wrong roster.
      if (scope !== effCompany) return;
      contacts = items.map((c) => ({
        value: c.id,
        label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
      }));
      if (fContact && !items.some((c) => c.id === fContact)) {
        if (loadedScope !== null) {
          fContact = "";
          contactCleared = true;
        }
      } else if (fContact) {
        contactCleared = false;
      }
      loadedScope = scope;
    })();
  });

  // --- inline-create behind the pickers (docs/UX.md) ------------------------------------- //
  // Every dialog lives at the bottom of this file and posts to `interactionActions`, which the
  // host already spreads — the client and project ＋ used to be handlers a host page passed in,
  // so they existed on `/interactions` and on none of the panels this same form renders in.
  // The gates are the API's own keys, not `!isPortal` (§15): a control that would 403 is never
  // drawn.
  const canCreateCompany = $derived(can(page.data.user, "companies.company.write"));
  // Projects have no separate create permission — writing one is creating one.
  const canCreateProject = $derived(can(page.data.user, "projects.project.write"));
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  const canCreateContact = $derived(can(page.data.user, "contacts.contact.write"));
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  let companyCreateOpen = $state(false);
  let projectCreateOpen = $state(false);
  let qcOpen = $state(false);
  let qcName = $state("");
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);
  // Offer to link the new contact to the upload's client (#247), checked by default: resolve
  // the client's name from the loaded lookups, else fetch it — an id can't label the box.
  let contactLinkCompany = $state<{ id: string; name: string } | null>(null);
  async function resolveLinkCompany(): Promise<{ id: string; name: string } | null> {
    const id = effCompany;
    if (!id) return null;
    let name = companies.find((c) => c.value === id)?.label ?? "";
    if (!name) {
      try {
        const response = await fetch(`/api/v1/companies/${id}`, {
          headers: { accept: "application/json" },
        });
        if (response.ok) name = ((await response.json()).name as string | undefined) ?? "";
      } catch {
        name = "";
      }
    }
    return name ? { id, name } : null;
  }
  async function quickCreateContact(query: string) {
    qcName = query;
    contactLinkCompany = await resolveLinkCompany();
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
  /**
   * `page.form.inlineCreated` outlives the dialog that produced it: an id already on `page.form`
   * at mount was answered by somebody else, so it starts out acknowledged and only a create made
   * *by this instance* is acted on (docs/UX.md). Deliberate initial capture.
   */
  let handledCreate = $state((page.form?.inlineCreated as { id?: string } | undefined)?.id ?? "");
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
        contacts = [...contacts, { value: created.id, label: created.name || qcName || "—" }];
      }
      fContact = created.id;
      contactCleared = false;
      // The shared roster cache must not outlive the person it does not know about (#290):
      // the next form to open would offer a picker missing the contact just created here.
      forgetContacts();
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
    } else if (created.slot === "eml_company") {
      handledCreate = created.id;
      if (!companies.some((c) => c.value === created.id)) {
        companies = [
          ...companies,
          { value: created.id, label: created.name ?? (companyQuery || "—") },
        ];
      }
      fCompany = created.id;
    } else if (created.slot === "eml_project") {
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
          oncreate={canCreateCompany
            ? (query) => {
                companyQuery = query;
                companyCreateOpen = true;
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
          oncreate={canCreateProject
            ? (query) => {
                projectQuery = query;
                projectCreateOpen = true;
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
        onselect={(v) => {
          fContact = v;
          contactCleared = false;
        }}
        oncreate={canCreateContact ? (query) => void quickCreateContact(query) : undefined}
        id="eml-contact"
      />
      {#if contactCleared && !fContact}
        <span class="mt-1 block text-xs text-text-muted"
          >{t("interactions.field.contact_recheck")}</span
        >
      {/if}
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
  linkCompany={contactLinkCompany}
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

<CompanyQuickCreate
  bind:open={companyCreateOpen}
  name={companyQuery}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionCompany"
  pickerSlot="eml_company"
  error={(page.form?.qcError as string | undefined) ?? null}
/>

<!-- The client roster is the one this form already loaded, so the ＋ costs no second fetch,
     and the upload's own client rides along as the new project's (#247). -->
<ProjectQuickCreate
  bind:open={projectCreateOpen}
  name={projectQuery}
  {companies}
  companyId={effCompany}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionProject"
  pickerSlot="eml_project"
  error={(page.form?.qcError as string | undefined) ?? null}
/>
