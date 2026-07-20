<script lang="ts">
  /**
   * The manual contactmoment form (meeting / call / note), rendered inside a `Modal` by the
   * panel body. One save button; posts to the **host page's** `?/createInteraction` or
   * `?/updateInteraction` action (a panel edits through its host, docs/UX.md).
   *
   * The date+time post as the tenant's wall clock (naive); the API attaches the org zone, so
   * a hand-typed 14:00 lands on the same timeline instant the reader sees.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";

  import { minutesBetween } from "$lib/modules/time/duration";
  import { formatMinutes } from "$lib/modules/time/format";

  import {
    type InteractionItem,
    type InteractionKindDef,
    instantToLocal,
    kindLabel,
    manualKinds,
  } from "./format";
  import { loadLinkLookups, type LinkOption, type ProjectOption, type TaskOption } from "./lookups";

  let {
    interaction = null,
    prefill = {},
    mentions = [],
    onsaved,
    oncreatecompany,
    oncreateproject,
  }: {
    /** Existing row when editing; null for create. */
    interaction?: InteractionItem | null;
    /** The host entity's link, stamped on new rows (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    /** Org members offered by the note editor's @ autocomplete (#151). */
    mentions?: { id: string; name: string }[];
    onsaved?: () => void;
    /**
     * Inline-create for the unpinned company / project pickers (docs/UX.md): the host page
     * owns the dialogs (slots `interaction_company` / `interaction_project`), so it passes a
     * handler that receives what was typed. Absent → the picker offers no ＋.
     */
    oncreatecompany?: (query: string) => void;
    oncreateproject?: (query: string) => void;
  } = $props();

  const local = interaction ? instantToLocal(interaction.occurred_at) : null;
  let kind = $state(interaction?.kind ?? "");
  let date = $state(local?.date ?? new Date().toISOString().slice(0, 10));
  let time = $state(local?.time ?? "");
  let error = $state("");

  // Kinds are tenant-defined (#174), fetched once per session (module-level cache). The
  // list shows the active ones, plus the row's own kind when editing — a deactivated kind
  // must stay pickable on the rows that already carry it.
  const locale = $derived((page.data.locale as string | undefined) ?? "nl");
  let allKinds = $state<InteractionKindDef[]>([]);
  $effect(() => {
    void manualKinds().then((fetched) => {
      allKinds = fetched;
      if (!kind) kind = fetched.find((k) => k.active)?.key ?? "";
    });
  });
  const kinds = $derived(allKinds.filter((k) => k.active || k.key === kind));

  const DIRECTIONS = ["none", "inbound", "outbound"] as const;

  // A dimension is *pinned* when the host page already fixed it (its hidden prefill input);
  // an unpinned one gets a picker below (#183 follow-up) — all three on the Interacties page,
  // project+task on a company page, none on a task page. contact_id is always a picker (#173).
  const pinned = (field: string) =>
    typeof prefill[field] === "string" && (prefill[field] as string).length > 0;
  const showCompany = $derived(!interaction && !pinned("company_id"));
  const showProject = $derived(!interaction && !pinned("project_id"));
  const showTask = $derived(!interaction && !pinned("task_id"));
  const showLinkPickers = $derived(showCompany || showProject || showTask);

  // Pinned dims stay hidden inputs; unpinned ones (and contact) are pickers, so no name clash.
  const links = $derived(
    interaction
      ? {}
      : Object.fromEntries(
          Object.entries(prefill).filter(
            ([field, v]) =>
              field !== "contact_id" &&
              !(field === "company_id" && showCompany) &&
              !(field === "project_id" && showProject) &&
              !(field === "task_id" && showTask) &&
              typeof v === "string" &&
              v.length > 0,
          ),
        ),
  );

  // --- assign to client / project / task (#183 follow-up) ------------------------------- //
  let fCompany = $state("");
  let fProject = $state("");
  let fTask = $state("");
  let linkCompanies = $state<LinkOption[]>([]);
  let linkProjects = $state<ProjectOption[]>([]);
  let linkTasks = $state<TaskOption[]>([]);
  $effect(() => {
    if (!showLinkPickers) return;
    void loadLinkLookups().then((l) => {
      linkCompanies = l.companies;
      linkProjects = l.projects;
      linkTasks = l.tasks;
    });
  });
  // Cascade the way the move dialog does: a client narrows projects, a project narrows tasks,
  // and picking deeper backfills above — accounting for a dim the host already pinned.
  const effCompany = $derived(
    fCompany || (typeof prefill.company_id === "string" ? prefill.company_id : ""),
  );
  const effProject = $derived(
    fProject || (typeof prefill.project_id === "string" ? prefill.project_id : ""),
  );
  const projectOptions = $derived(
    effCompany
      ? linkProjects.filter((p) => !p.company_id || p.company_id === effCompany)
      : linkProjects,
  );
  const taskOptions = $derived(
    effProject ? linkTasks.filter((task) => task.project_id === effProject) : linkTasks,
  );
  function onProjectPicked(id: string) {
    fProject = id;
    const project = linkProjects.find((p) => p.value === id);
    if (project?.company_id && showCompany) fCompany = project.company_id;
    if (fTask && linkTasks.find((task) => task.value === fTask)?.project_id !== id) fTask = "";
  }
  function onTaskPicked(id: string) {
    fTask = id;
    const task = linkTasks.find((option) => option.value === id);
    if (task?.project_id) onProjectPicked(task.project_id);
  }

  // --- contact person (#173): pick, clear, or inline-create — never leave the form ------- //
  const hostCompanyId = $derived(
    interaction?.company_id ?? (typeof prefill.company_id === "string" ? prefill.company_id : null),
  );
  // Deliberate initial capture: the host keys this form per row, so props never swap in place.
  // svelte-ignore state_referenced_locally
  let contactId = $state(
    interaction?.contact_id ??
      (typeof prefill.contact_id === "string" ? prefill.contact_id : "") ??
      "",
  );
  let contactOptions = $state<{ value: string; label: string; hint?: string; company?: string }[]>(
    [],
  );
  $effect(() => {
    // Host company's roster first; an org without links there falls back to all contacts.
    const scope = hostCompanyId ? `&company_id=${hostCompanyId}` : "";
    void (async () => {
      let response = await fetch(`/api/v1/contacts?limit=200${scope}`, {
        headers: { accept: "application/json" },
      });
      interface ContactRow {
        id: string;
        first_name: string;
        last_name?: string | null;
        email?: string | null;
        companies?: { name: string }[];
      }
      let items: ContactRow[] = response.ok ? ((await response.json()).items ?? []) : [];
      if (items.length === 0 && scope) {
        response = await fetch("/api/v1/contacts?limit=200", {
          headers: { accept: "application/json" },
        });
        items = response.ok ? ((await response.json()).items ?? []) : [];
      }
      contactOptions = items.map((c) => ({
        value: c.id,
        label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
        hint: c.email ?? undefined,
        company: c.companies?.[0]?.name,
      }));
      // The row's own contact stays pickable even when outside the fetched scope.
      if (contactId && interaction?.contact_name && !items.some((c) => c.id === contactId)) {
        contactOptions = [{ value: contactId, label: interaction.contact_name }, ...contactOptions];
      }
    })();
  });

  // The note's @ autocomplete offers both kinds (#165): colleagues (the host's members) and
  // the same host-scoped contacts the picker above already fetched — one fetch serves both.
  const editorMentions = $derived([
    ...mentions.map((m) => ({ ...m, kind: "user" as const })),
    ...contactOptions.map((c) => ({
      id: c.value,
      name: c.label,
      kind: "contact" as const,
      subtitle: c.company ?? c.hint,
    })),
  ]);

  // --- "Voeg aan mijn uren toe" (#175): a linked time entry, saved with the interaction.
  // Create-only (an existing row's hours live on the timesheet) and not for notes, which
  // have no time-spent concept. The moment's own time field doubles as the entry's start when
  // checked (#184), so there's one clock, not two — just add an end and read off the duration.
  const canLogTime = $derived(!interaction && kind !== "note");
  let logTime = $state(false);
  let logEnd = $state("");
  const logMinutes = $derived(time && logEnd ? minutesBetween(time, logEnd) : null);

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
  // Company/project quick-create (docs/UX.md): the dialogs live on the host page; remember
  // what was typed so the auto-selected option can be labelled before the lookups refresh.
  let companyQuery = $state("");
  let projectQuery = $state("");
  function quickCreateCompany(query: string) {
    companyQuery = query;
    oncreatecompany?.(query);
  }
  function quickCreateProject(query: string) {
    projectQuery = query;
    oncreateproject?.(query);
  }
  // A quick-create action answers with the new row's id; auto-select it in the picker that
  // asked — the slot names are the contract with the host page's dialogs (docs/UX.md).
  let handledCreate = $state("");
  $effect(() => {
    const created = page.form?.inlineCreated as
      { slot: string; id: string; name?: string; company_id?: string | null } | undefined;
    if (!created || created.id === handledCreate) return;
    if (created.slot === "interaction_contact") {
      handledCreate = created.id;
      if (!contactOptions.some((c) => c.value === created.id)) {
        contactOptions = [...contactOptions, { value: created.id, label: qcName || "—" }];
      }
      contactId = created.id;
    } else if (created.slot === "interaction_company") {
      handledCreate = created.id;
      if (!linkCompanies.some((c) => c.value === created.id)) {
        linkCompanies = [...linkCompanies, { value: created.id, label: companyQuery || "—" }];
      }
      fCompany = created.id;
    } else if (created.slot === "interaction_project") {
      handledCreate = created.id;
      if (!linkProjects.some((p) => p.value === created.id)) {
        linkProjects = [
          ...linkProjects,
          {
            value: created.id,
            label: created.name ?? (projectQuery || "—"),
            company_id: created.company_id ?? null,
          },
        ];
      }
      // Reuse the picker's own cascade so a project created under a client backfills it.
      onProjectPicked(created.id);
    }
  });
</script>

<form
  method="POST"
  action={interaction ? "?/updateInteraction" : "?/createInteraction"}
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
  {#if interaction}
    <input type="hidden" name="id" value={interaction.id} />
  {/if}
  {#each Object.entries(links) as [field, value] (field)}
    <input type="hidden" name={field} {value} />
  {/each}

  <div class="grid gap-4 sm:grid-cols-2">
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.kind")}</span>
      <select
        name="kind"
        bind:value={kind}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each kinds as option (option.key)}
          <option value={option.key}>{kindLabel(option, locale)}</option>
        {/each}
      </select>
    </label>
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.date")}</span>
      <DateInput name="occurred_date" bind:value={date} required />
    </label>
  </div>

  {#if canLogTime}
    <!-- Above the title (#184): checking it turns the moment's time into the entry's start. -->
    <label class="flex items-center gap-2 text-sm text-text">
      <input type="checkbox" name="log_time" value="1" bind:checked={logTime} />
      {t("interactions.log_time")}
    </label>
  {/if}

  <!-- One clock: the moment's time, relabelled Start and paired with an end when logging (#184). -->
  <div class="flex flex-wrap items-end gap-3">
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">
        {logTime ? t("time.field.start") : t("interactions.field.time")}
      </span>
      <TimeInput name="occurred_time" bind:value={time} required={logTime} />
    </label>
    {#if logTime}
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("time.field.end")}</span>
        <TimeInput name="log_end" bind:value={logEnd} required />
      </label>
      <span
        class="pb-2 text-sm font-semibold tabular-nums {logMinutes
          ? 'text-brand'
          : 'text-text-muted'}"
      >
        {logMinutes != null ? t("time.worked", { duration: formatMinutes(logMinutes) }) : "—"}
      </span>
    {/if}
  </div>
  {#if logTime}
    <p class="-mt-2 text-xs text-text-muted">{t("interactions.log_time_hint")}</p>
  {/if}

  <label class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.subject")}</span>
    <input
      name="subject"
      value={interaction?.subject ?? ""}
      required
      maxlength="500"
      class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
    />
  </label>

  {#if showLinkPickers}
    <!-- Assign the moment to a client / project / task while logging (#183 follow-up); only the
         dimensions the host page hasn't already pinned are offered. -->
    <div class="grid gap-4 sm:grid-cols-2">
      {#if showCompany}
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.company")}</span>
          <Combobox
            items={linkCompanies}
            name="company_id"
            value={fCompany}
            placeholder={t("common.none")}
            onselect={(v) => (fCompany = v)}
            oncreate={oncreatecompany ? quickCreateCompany : undefined}
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
            oncreate={oncreateproject ? quickCreateProject : undefined}
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
          />
        </label>
      {/if}
    </div>
  {/if}

  <div class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.contact")}</span>
    <Combobox
      items={contactOptions}
      name="contact_id"
      bind:value={contactId}
      placeholder={t("interactions.field.contact_placeholder")}
      oncreate={(query) => void quickCreateContact(query)}
    />
  </div>

  {#if kind === "call"}
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.direction")}</span>
      <select
        name="direction"
        value={interaction?.direction ?? "none"}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each DIRECTIONS as option (option)}
          <option value={option}>{t(`interactions.direction.${option}`)}</option>
        {/each}
      </select>
    </label>
  {/if}

  <div class="text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.notes")}</span>
    <RichTextEditor
      name="body_text"
      value={interaction?.body_text ?? ""}
      rows={4}
      mentions={editorMentions}
    />
  </div>

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button
      type="submit"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("common.save")}
    </button>
  </div>
</form>

<ContactQuickCreate
  bind:open={qcOpen}
  name={qcName}
  definitions={contactDefinitions ?? []}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionContact"
  pickerSlot="interaction_contact"
  error={(page.form?.qcError as string | undefined) ?? null}
/>
