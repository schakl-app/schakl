<script lang="ts">
  /**
   * The manual contactmoment form (meeting / call / note), rendered inside a `Modal` by the
   * panel body. One save button; posts to the **host page's** `?/createInteraction` or
   * `?/updateInteraction` action (a panel edits through its host, docs/UX.md).
   *
   * The date+time post as the tenant's wall clock (naive); the API attaches the org zone, so
   * a hand-typed 14:00 lands on the same timeline instant the reader sees.
   *
   * Every picker here creates what it cannot find (docs/UX.md — per-picker definition of done),
   * and does it **from inside this component**: the client and project ＋ used to be handlers the
   * host page passed in, which meant they existed on `/interactions` and nowhere else — not even
   * on the edit modal three lines below the create one, and on none of the company / project /
   * contact / task pages, where this same form renders through the panel. The dialogs post to
   * `interactionActions`, which every one of those hosts already spreads, so the ＋ arrives with
   * the panel instead of having to be re-wired per screen.
   */
  import { Plus } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import { orgToday } from "$lib/core/today";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import ProjectQuickCreate from "$lib/modules/projects/ProjectQuickCreate.svelte";
  import { canWriteTask } from "$lib/modules/tasks/permissions";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  import { minutesBetween } from "$lib/modules/time/duration";
  import { formatMinutes } from "$lib/modules/time/format";
  import { companyArchivedLabel } from "$lib/modules/companies/picker";
  import { projectArchivedLabel } from "$lib/modules/projects/picker";

  import {
    type InteractionItem,
    type InteractionKindDef,
    instantToLocal,
    interactionKinds,
    kindLabel,
    manualKinds,
    PROTECTED_KIND,
  } from "./format";
  import ContactChips from "./ContactChips.svelte";
  import {
    loadLinkLookups,
    splitLinkOptions,
    type LinkOption,
    type ProjectOption,
    type TaskOption,
  } from "./lookups";
  import { ContactRoster, initialContacts } from "./roster.svelte";

  let {
    interaction = null,
    prefill = {},
    mentions = [],
    onsaved,
  }: {
    /** Existing row when editing; null for create. */
    interaction?: InteractionItem | null;
    /** The host entity's link, stamped on new rows (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    /** Org members offered by the note editor's @ autocomplete (#151). */
    mentions?: { id: string; name: string }[];
    onsaved?: () => void;
  } = $props();

  // Deliberate initial capture: the host keys this form per row, so props never swap in place.
  // svelte-ignore state_referenced_locally
  const own = interaction;

  const local = interaction ? instantToLocal(interaction.occurred_at) : null;
  let kind = $state(interaction?.kind ?? "");
  let date = $state(local?.date ?? orgToday());
  let time = $state(local?.time ?? "");
  let error = $state("");

  const busy = new InFlight();

  // Kinds are tenant-defined (#174), fetched once per session (module-level cache). The
  // list shows the active ones, plus the row's own kind when editing — a deactivated kind
  // must stay pickable on the rows that already carry it.
  const locale = $derived((page.data.locale as string | undefined) ?? "nl");
  // Editing an uploaded email (#262): `email` is not a kind anyone may pick, so it is shown
  // rather than offered — a select without it would silently re-type the message on save.
  const emailRow = $derived(interaction?.kind === PROTECTED_KIND);
  let allKinds = $state<InteractionKindDef[]>([]);
  $effect(() => {
    void (emailRow ? interactionKinds() : manualKinds()).then((fetched) => {
      allKinds = fetched;
      if (!kind) kind = fetched.find((k) => k.active)?.key ?? "";
    });
  });
  const kinds = $derived(allKinds.filter((k) => k.active || k.key === kind));
  const kindDef = $derived(allKinds.find((k) => k.key === kind));

  const DIRECTIONS = ["none", "inbound", "outbound"] as const;

  // A dimension is *pinned* when the host page already fixed it (its hidden prefill input);
  // an unpinned one gets a picker below (#183 follow-up) — all three on the Interacties page,
  // project+task on a company page, client+project on a task page (preset from the task's own
  // links, see below). contact_id is always a picker (#173).
  //
  // `prefill` is a **create**-time concept: what the host pinned onto a new row. An edit opens
  // on the row's own links and may repoint them (#263) — until now the only way to fix a
  // mis-filed moment was the separate Verplaatsen dialog, two menus away from "bewerken".
  const pinned = (field: string) =>
    !own && typeof prefill[field] === "string" && (prefill[field] as string).length > 0;
  const showCompany = $derived(!pinned("company_id"));
  const showProject = $derived(!pinned("project_id"));
  const showTask = $derived(!pinned("task_id"));
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
  let fCompany = $state(own?.company_id ?? "");
  let fProject = $state(own?.project_id ?? "");
  let fTask = $state(own?.task_id ?? "");
  /**
   * Which link a kind leads with (#263). A phone call or a meeting is primarily *with a
   * person*; a note is primarily *about work*. So the contact picker is up front for every
   * kind, and the client/project/task block only opens by default where it is the point — a
   * logged call no longer pays three empty dropdowns of vertical space before anything at all
   * is picked, and reaches them in one click when it needs them.
   *
   * Kinds are tenant data (#174) and carry no `link_hint` of their own yet, so the **known**
   * system keys drive this and anything a tenant added falls back to the open default — the
   * same "known keys get the extra, new ones get the neutral one" rule `kindIcon` already uses.
   */
  const PERSON_KINDS = new Set(["call", "email", "online_meeting", "physical_meeting"]);
  /** null = follow the kind; true/false = the user said so, and keeps saying so. */
  let linksExpanded = $state<boolean | null>(null);
  const hasLink = $derived(Boolean(fCompany || fProject || fTask));
  const linksOpen = $derived(linksExpanded ?? (hasLink || !PERSON_KINDS.has(kind)));

  let linkCompanies = $state<LinkOption[]>([]);
  let linkProjects = $state<ProjectOption[]>([]);
  let linkTasks = $state<TaskOption[]>([]);
  let lookupsLoaded = false;
  $effect(() => {
    // Nothing is fetched until the block is actually open (docs/PERFORMANCE.md): a logged call
    // that never links anywhere must not cost three lookups.
    if (!showLinkPickers || !linksOpen || lookupsLoaded) return;
    lookupsLoaded = true;
    // Only host-pinned dims scope the fetch (#222): they never change, while an unpinned
    // picker's own pick must not re-fetch — the derivations below narrow client-side.
    void loadLinkLookups({
      companyId: pinned("company_id") ? (prefill.company_id as string) : null,
      projectId: pinned("project_id") ? (prefill.project_id as string) : null,
    }).then((l) => {
      // An edited row's own links stay labelled even when they fall outside the fetched 200 —
      // the same rule the contact picker below follows.
      linkCompanies =
        own?.company_id && own.company_name && !l.companies.some((c) => c.value === own.company_id)
          ? [{ value: own.company_id, label: own.company_name }, ...l.companies]
          : l.companies;
      linkProjects =
        own?.project_id && own.project_name && !l.projects.some((p) => p.value === own.project_id)
          ? [
              {
                value: own.project_id,
                label: own.project_name,
                company_id: own.company_id ?? null,
              },
              ...l.projects,
            ]
          : l.projects;
      linkTasks =
        own?.task_id && own.task_title && !l.tasks.some((task) => task.value === own.task_id)
          ? [
              {
                value: own.task_id,
                label: own.task_title,
                project_id: own.project_id ?? null,
                company_id: own.company_id ?? null,
                // Unknown, and it never has to be known: this option only exists while editing
                // an existing moment, and the close checkbox is create-only (`!own` below).
                assignees: [],
                assignee_user_id: null,
              },
              ...l.tasks,
            ]
          : l.tasks;
    });
  });
  // A pinned task/project implies the levels above it: resolve the host row once, when the
  // form opens, and preset the pickers — visible and repointable, unlike a pinned dim. The
  // API derives a missing client from the task on save either way, but the form should show
  // where the moment will land, not an empty picker.
  $effect(() => {
    if (!showLinkPickers) return;
    const pinnedTask = pinned("task_id") ? (prefill.task_id as string) : "";
    const pinnedProject = pinned("project_id") ? (prefill.project_id as string) : "";
    if (!pinnedTask && !pinnedProject) return;
    void (async () => {
      let company: string;
      let project = "";
      if (pinnedTask) {
        const response = await fetch(`/api/v1/tasks/${pinnedTask}`, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) return;
        const task = await response.json();
        company = task.company_id ?? "";
        project = task.project_id ?? "";
      } else {
        const response = await fetch(`/api/v1/projects/${pinnedProject}`, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) return;
        company = (await response.json()).company_id ?? "";
      }
      if (project && showProject && !fProject) fProject = project;
      if (company && showCompany && !fCompany) fCompany = company;
    })();
  });
  // Cascade the way the move dialog does: a client narrows projects, a project narrows tasks,
  // and picking deeper backfills above — accounting for a dim the host already pinned.
  const effCompany = $derived(
    fCompany || (typeof prefill.company_id === "string" ? prefill.company_id : ""),
  );
  const effProject = $derived(
    fProject || (typeof prefill.project_id === "string" ? prefill.project_id : ""),
  );
  // The API derives `company_id` from a project/task link on write (`_resolve_links`,
  // models.py:13-15), so the form stops *asking* for it the moment either is picked: it shows
  // the client the moment will land on and posts that same id, instead of offering a fourth
  // dropdown the write path would overrule anyway.
  const companyDerived = $derived(Boolean(fProject || fTask));
  const companyLabel = $derived(linkCompanies.find((c) => c.value === fCompany)?.label ?? "");

  // The cascade narrows *which* rows each picker may offer; the split then decides which of
  // them are suggested. A moment filed against a client the agency stopped working for, a
  // finished project or a closed task is an ordinary thing to record — so none of them is
  // dropped, they move behind the search wearing their status (`lookups.splitLinkOptions`).
  const linkSplit = $derived(
    splitLinkOptions(
      {
        companies: linkCompanies,
        projects: effCompany
          ? linkProjects.filter((p) => !p.company_id || p.company_id === effCompany)
          : linkProjects,
        tasks: effProject
          ? linkTasks.filter((task) => task.project_id === effProject)
          : effCompany
            ? linkTasks.filter((task) => !task.company_id || task.company_id === effCompany)
            : linkTasks,
      },
      { companyId: fCompany, projectId: fProject, taskId: fTask },
    ),
  );
  const projectOptions = $derived(linkSplit.projects.live);
  const taskOptions = $derived(linkSplit.tasks.live);
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
    // A task filed straight under a client, with no project of its own, still fixes the client.
    else if (task?.company_id && showCompany) fCompany = task.company_id;
  }

  // --- close the picked task with this contact moment (#232, the approve dialog's #157
  // affordance on the plain create form) --------------------------------------------------- //
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
  // Offered once a task is picked; the guard mirrors the API (a close is a task write),
  // which stays the boundary. Create-only: the link pickers reach the edit form now (#263),
  // but `?/updateInteraction` runs no close — an existing row closes its task through the
  // panel's own CloseTaskDialog, and a checkbox that silently did nothing would be worse.
  // Per *task*: `tasks.task.write:own` means assignee, so the base key offered the box on every
  // colleague's task and the close came back refused. `linkTasks` carries the assignee for this.
  const canCloseTask = $derived(
    !own &&
      Boolean(fTask) &&
      canWriteTask(page.data.user, linkTasks.find((task) => task.value === fTask) ?? null),
  );
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

  // Create succeeded but the close PATCH bounced (e.g. a status policy): say exactly that —
  // a plain error here would read as "the save failed", which it did not.
  let closeFailedAfterCreate = $state(false);

  // --- who it was with (#173, a roster since #300): pick several, promote, inline-create ---- //
  // Deliberate initial capture: the host keys this form per row, so props never swap in place.
  // svelte-ignore state_referenced_locally
  const roster = new ContactRoster(initialContacts(interaction, prefill));
  /**
   * The options follow the moment's **effective** client — `effCompany`, so the client the host
   * pinned, the one picked in the block below, *and* the one backfilled from a project or task
   * pick all re-scope them. Everything else about that (keeping a stored chip pickable, dropping
   * one the *changed* client does not know, saying so) lives in `ContactRoster`, shared with the
   * move dialog and the .eml upload so the three can no longer drift apart.
   */
  $effect(() => {
    void roster.load(effCompany);
  });

  // The note's @ autocomplete offers both kinds (#165): colleagues (the host's members) and
  // the same host-scoped contacts the picker above already fetched — one fetch serves both.
  const editorMentions = $derived([
    ...mentions.map((m) => ({ ...m, kind: "user" as const })),
    ...roster.options.map((c) => ({
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
  // Offer to link the new contact to the moment's client (#247), checked by default: resolve
  // the client's name from the lookups already loaded, else fetch it — an id can't label the box.
  let contactLinkCompany = $state<{ id: string; name: string } | null>(null);
  async function resolveLinkCompany(): Promise<{ id: string; name: string } | null> {
    const id = effCompany;
    if (!id) return null;
    let name =
      linkCompanies.find((c) => c.value === id)?.label ??
      (interaction?.company_id === id ? (interaction?.company_name ?? "") : "");
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
  // The client / project / task ＋ (docs/UX.md). Each dialog lives at the bottom of this file
  // and posts to `interactionActions`, so it works on every host that renders the panel.
  // What was typed is remembered so the auto-selected option can be labelled before the
  // lookups refresh. The gates are the API's own keys, not `!isPortal` (§15): the timeline is
  // client-reachable, and a control that would 403 must never be drawn.
  const canCreateCompany = $derived(can(page.data.user, "companies.company.write"));
  // Projects have no separate create permission — writing one is creating one.
  const canCreateProject = $derived(can(page.data.user, "projects.project.write"));
  const canCreateTask = $derived(can(page.data.user, "tasks.task.create"));
  const canCreateContact = $derived(can(page.data.user, "contacts.contact.write"));
  let companyQuery = $state("");
  let companyCreateOpen = $state(false);
  let projectQuery = $state("");
  let projectCreateOpen = $state(false);
  let taskDraft = $state("");
  let taskCreateOpen = $state(false);
  /**
   * A quick-create action answers with the new row's id; auto-select it in the picker that
   * asked — the slot names are this file's own contract with the dialogs below.
   *
   * `page.form.inlineCreated` outlives the dialog that produced it, and the hosts key this form
   * per row: create a project while logging one moment, close without saving, open the next, and
   * a fresh instance would read the *previous* answer and pre-select that project — plausible,
   * and filed onto the wrong row the moment the user saves. An id already on `page.form` at
   * mount was therefore answered by somebody else, so it starts out acknowledged. Deliberate
   * initial capture: only a create made *by this instance* arrives after mount.
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
          assignees?: { user_id: string }[] | null;
          assignee_user_id?: string | null;
        }
      | undefined;
    if (!created || created.id === handledCreate) return;
    if (created.slot === "interaction_contact") {
      handledCreate = created.id;
      // Offers them, adds the chip, and drops the shared per-scope cache so the next form to
      // open knows about them too (#290).
      roster.created(created.id, created.name || qcName || "—");
    } else if (created.slot === "interaction_company") {
      handledCreate = created.id;
      if (!linkCompanies.some((c) => c.value === created.id)) {
        linkCompanies = [
          ...linkCompanies,
          { value: created.id, label: created.name ?? (companyQuery || "—") },
        ];
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
    } else if (created.slot === "interaction_task") {
      handledCreate = created.id;
      if (!linkTasks.some((task) => task.value === created.id)) {
        linkTasks = [
          ...linkTasks,
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
    }
  });
</script>

<form
  method="POST"
  action={interaction ? "?/updateInteraction" : "?/createInteraction"}
  class="space-y-4"
  use:enhance={busy.wrap("", () => async ({ result, update }) => {
    if (result.type === "failure") {
      closeFailedAfterCreate = Boolean(result.data?.createdButCloseFailed);
      error = String(result.data?.error ?? "errors.validation");
      return;
    }
    error = "";
    closeFailedAfterCreate = false;
    await update({ reset: false });
    onsaved?.();
  })}
>
  {#if interaction}
    <input type="hidden" name="id" value={interaction.id} />
  {/if}
  {#each Object.entries(links) as [field, value] (field)}
    <input type="hidden" name={field} {value} />
  {/each}

  <div class="grid gap-4 sm:grid-cols-2">
    {#if emailRow}
      <!-- An email stays an email (#262): shown, not offered, and posted unchanged. -->
      <div class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.kind")}</span>
        <input type="hidden" name="kind" value={kind} />
        <p class="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted">
          {kindDef ? kindLabel(kindDef, locale) : t("interactions.kind.email")}
        </p>
      </div>
    {:else}
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
    {/if}
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

  <!-- Optional (#22 follow-up): a logged phone call is titled by what it *is*, and every
       surface already falls back to the kind's label — so the placeholder shows the title the
       row will carry when this is left empty, rather than demanding one be invented before
       the notes below can be written. -->
  <label class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.subject")}</span>
    <input
      name="subject"
      value={interaction?.subject ?? ""}
      placeholder={kindDef ? kindLabel(kindDef, locale) : ""}
      maxlength="500"
      class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
    />
  </label>

  <!-- Who the moment was *with* comes first (#263): for a logged call or a meeting that is the
       primary fact, not an afterthought below three organisational pickers. Several of them
       since #300 — a meeting is with the people who were in it. -->
  <div class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.contacts")}</span>
    <ContactChips
      {roster}
      id="interaction-contacts"
      oncreate={canCreateContact ? (query) => void quickCreateContact(query) : undefined}
    />
  </div>

  {#if showLinkPickers && !linksOpen}
    <!-- A call or a meeting opens on the person alone and reaches the rest in one click (#263).
         A note — work, not a conversation — opens with these already unfolded. -->
    <button
      type="button"
      onclick={() => (linksExpanded = true)}
      class="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-2 text-sm text-text-muted hover:border-brand hover:text-brand"
    >
      <Plus size={14} aria-hidden="true" />
      {t("interactions.link.add")}
    </button>
  {/if}

  {#if showLinkPickers && linksOpen}
    <!-- Assign the moment to a client / project / task while logging (#183 follow-up); only the
         dimensions the host page hasn't already pinned are offered. -->
    <span class="block text-sm font-medium text-text">{t("interactions.link.title")}</span>
    <div class="grid gap-4 sm:grid-cols-2">
      {#if showCompany}
        {#if companyDerived}
          <!-- Derived, not asked (#263): the write path fills the client in from the project or
               the task, so the form shows where the moment lands instead of a fourth dropdown. -->
          <div class="block text-sm">
            <span class="mb-1 block font-medium text-text">{t("interactions.field.company")}</span>
            <p class="rounded-lg border border-dashed border-border px-3 py-2 text-sm text-text">
              {companyLabel || t("common.none")}
              <span class="mt-0.5 block text-xs text-text-muted">
                {fTask
                  ? t("interactions.link.company_from_task")
                  : t("interactions.link.company_from_project")}
              </span>
            </p>
            <input type="hidden" name="company_id" value={fCompany} />
          </div>
        {:else}
          <label class="block text-sm">
            <span class="mb-1 block font-medium text-text">{t("interactions.field.company")}</span>
            <Combobox
              items={linkSplit.companies.live}
              archived={linkSplit.companies.retired}
              archivedLabel={companyArchivedLabel()}
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
            />
          </label>
        {/if}
      {/if}
      {#if showProject}
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.project")}</span>
          <Combobox
            items={projectOptions}
            archived={linkSplit.projects.retired}
            archivedLabel={projectArchivedLabel()}
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
          />
        </label>
      {/if}
      {#if showTask}
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.task")}</span>
          <Combobox
            items={taskOptions}
            archived={linkSplit.tasks.retired}
            archivedLabel={t("tasks.picker.archived")}
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
          />
        </label>
      {/if}
    </div>

    {#if canCloseTask}
      <!-- Close the picked task with this contact moment (#232): the approve dialog's #157
           affordance while logging; the status pick mirrors CloseTaskDialog. -->
      <div class="space-y-2 rounded-lg border border-border p-3">
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="close_task" value="1" bind:checked={closeTask} />
          {t("interactions.close_task")}
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

  {#if emailRow}
    <!-- A received message is not ours to re-type (#262): shown for context while the links
         are corrected, and deliberately not posted — the API leaves an unsent field alone. -->
    {#if interaction?.body_markdown || interaction?.body_text}
      <div class="text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.eml.message")}</span>
        {#if interaction.body_markdown}
          <!-- Converted from the message's own HTML part, so this reads as it was sent. -->
          <div
            class="max-h-40 overflow-y-auto rounded-lg border border-border bg-surface px-3 py-2"
          >
            <Markdown value={interaction.body_markdown} images class="break-words" />
          </div>
        {:else}
          <p
            class="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted"
          >
            {interaction.body_text}
          </p>
        {/if}
      </div>
    {/if}
  {:else}
    <div class="text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.notes")}</span>
      <!-- #task references (#237) resolve against the moment's own links: the picked (or
           stored) project, else the client — same deeper-link-wins rule as the task picker. -->
      <RichTextEditor
        name="body_text"
        value={interaction?.body_text ?? ""}
        rows={4}
        mentions={editorMentions}
        scope={{
          companyId: (interaction?.company_id ?? effCompany) || null,
          projectId: (interaction?.project_id ?? effProject) || null,
        }}
      />
    </div>
  {/if}

  {#if closeFailedAfterCreate}
    <p class="text-sm text-red-600">{t("interactions.close_after_create_failed")}</p>
  {/if}
  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <Button type="submit" loading={busy.active}>
      {t("common.save")}
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
  pickerSlot="interaction_contact"
  error={(page.form?.qcError as string | undefined) ?? null}
/>

<CompanyQuickCreate
  bind:open={companyCreateOpen}
  name={companyQuery}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionCompany"
  pickerSlot="interaction_company"
  error={(page.form?.qcError as string | undefined) ?? null}
/>

<!-- The client roster is the one the link block already loaded, so the ＋ costs no second
     fetch, and the moment's own client rides along as the new project's (#247). -->
<ProjectQuickCreate
  bind:open={projectCreateOpen}
  name={projectQuery}
  companies={linkCompanies}
  companyId={effCompany}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionProject"
  pickerSlot="interaction_project"
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
  pickerSlot="interaction_task"
  error={(page.form?.qcError as string | undefined) ?? null}
/>
