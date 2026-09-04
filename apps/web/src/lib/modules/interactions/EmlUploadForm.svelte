<script lang="ts">
  /**
   * Log an email the timeline is missing: from its `.eml` export (#262), or straight out of the
   * caller's own Gmail by reference (#342). Either way you assign it to a client / project /
   * task / contact in the same step and save, and the API writes a row that reads exactly like
   * a synced one — only where the bytes came from differs.
   *
   * **One dialog, two sources, one set of pickers.** The half that changes is where the message
   * comes from; the half that matters — which client, which project, which task, who it was
   * with, and whether schakl should read it into that task — is identical, and duplicating it
   * per source is how the second source ends up missing a field the first one has. That is the
   * shape of the bug #342 fixed one layer down (the AI fill-in was reachable only from review,
   * so the upload silently lacked it), so it is not a shape to repeat here.
   *
   * The Gmail half refuses to browse. It resolves a reference the caller already holds — a
   * link, a `Message-ID`, or the id of a conversation we logged part of — and never lists a
   * mailbox: a picker over arbitrary personal mail is the trust landmine `docs/GOOGLE.md` names,
   * and it would make "schakl only ever sees matched mail" untrue.
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
  import { Mail, Paperclip, Search } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import type { components } from "$lib/core/api/schema";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t, tn } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import ProjectQuickCreate from "$lib/modules/projects/ProjectQuickCreate.svelte";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";
  import TaskReviewDialog from "$lib/modules/tasks/TaskReviewDialog.svelte";
  import { fmtDateTime } from "$lib/core/format";
  import { companyArchivedLabel } from "$lib/modules/companies/picker";
  import { projectArchivedLabel } from "$lib/modules/projects/picker";

  import { canWriteTask } from "$lib/modules/tasks/permissions";

  import ContactChips from "./ContactChips.svelte";
  import TaskChips from "./TaskChips.svelte";
  import GmailMessagePicker from "./GmailMessagePicker.svelte";
  import {
    loadLinkLookups,
    splitLinkOptions,
    type LinkOption,
    type ProjectOption,
    type TaskOption,
  } from "./lookups";
  import { ContactRoster, initialContacts } from "./roster.svelte";

  let {
    prefill = {},
    threadId = null,
    gmailAvailable = null,
    onsaved,
  }: {
    /** The host entity's link, stamped on the uploaded row (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    /**
     * Open on the Gmail tab, filling the gaps in **this** conversation (#342). The id came off
     * a row the poller wrote, so asking about it is not new reach — it is the one reference
     * nobody has to copy out of a browser address bar, which is exactly the one that does not
     * survive the trip.
     */
    threadId?: string | null;
    /**
     * Whether this mailbox can actually be read (`/google/gmail/status`). `null` = the host did
     * not load it, and the permission stands in: a tab that always refuses is worse than no tab
     * (#253), but plumbing a status read into every panel that renders this dialog would tax
     * every open for a source most of them never use.
     */
    gmailAvailable?: boolean | null;
    onsaved?: () => void;
  } = $props();

  const busy = new InFlight();

  let filename = $state("");
  let error = $state("");
  let duplicate = $state(false);
  let skipped = $state(0);

  // --- where the message comes from (#342) --------------------------------------------- //
  type LookupResult = components["schemas"]["GmailLookupResult"];
  type Candidate = components["schemas"]["GmailCandidate"];
  const gmailOffered = $derived(gmailAvailable ?? can(page.data.user, "google.connection.manage"));
  // A thread to fill in is a Gmail question by construction; anything else opens on the file.
  let source = $state<"file" | "gmail">(threadId ? "gmail" : "file");
  let reference = $state("");
  // The search fields (#372), bound so a refinement keeps what was typed — `reset: false` on
  // its own would only preserve them across a *failure*, and narrowing a result set means
  // submitting the same form again with one field changed.
  let searchParticipant = $state("");
  let searchSubject = $state("");
  let searchAfter = $state("");
  let searchBefore = $state("");
  let lookup = $state<LookupResult | null>(null);
  let picked = $state<Candidate | null>(null);
  let gmailError = $state("");
  const gmail = $derived(source === "gmail" && gmailOffered);

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
  // A roster (`TaskChips`); the lead is what the fill-in offer and the review hand-back read.
  let fTasks = $state<string[]>([]);
  const fTask = $derived(fTasks[0] ?? "");
  const taskLabels: Record<string, string | null | undefined> = {};
  let companies = $state<LinkOption[]>([]);
  let projects = $state<ProjectOption[]>([]);
  let tasks = $state<TaskOption[]>([]);
  // svelte-ignore state_referenced_locally — the host keys this form; props never swap in place.
  const roster = new ContactRoster(initialContacts(null, prefill));

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
  const effTask = $derived(fTask || (typeof prefill.task_id === "string" ? prefill.task_id : ""));
  // The cascade decides *which* rows may be offered; the split decides which of them are
  // suggested. An archived client, a finished project and a closed task each drop behind the
  // search wearing their status rather than out of the picker (`lookups.splitLinkOptions`).
  const linkSplit = $derived(
    splitLinkOptions(
      {
        companies,
        projects: effCompany
          ? projects.filter((p) => !p.company_id || p.company_id === effCompany)
          : projects,
        tasks: effProject
          ? tasks.filter((task) => task.project_id === effProject)
          : effCompany
            ? tasks.filter((task) => !task.company_id || task.company_id === effCompany)
            : tasks,
      },
      { companyId: fCompany, projectId: fProject, taskId: fTask },
    ),
  );
  const projectOptions = $derived(linkSplit.projects.live);
  const taskOptions = $derived(linkSplit.tasks.live);
  function onProjectPicked(id: string) {
    fProject = id;
    const project = projects.find((p) => p.value === id);
    if (project?.company_id && showCompany) fCompany = project.company_id;
    // Every chip follows the project, not only the lead (`InteractionForm`'s rule).
    fTasks = fTasks.filter(
      (picked) => tasks.find((task) => task.value === picked)?.project_id === id,
    );
  }
  /** The lead changed (`TaskChips`' `onpick`): it fixes the levels above. */
  function onTaskPicked(id: string) {
    const task = tasks.find((option) => option.value === id);
    if (task?.project_id) onProjectPicked(task.project_id);
  }

  /**
   * The roster follows the upload's **effective** client, exactly as the manual form's does —
   * the host's pinned client, the one picked below, or the one backfilled from a project or
   * task pick. It used to read only the pinned one, so the picker on the Interacties page
   * listed every contact in the org however the message was being filed, and a client change
   * never narrowed it. `ContactRoster` is the one copy of that rule now (#300), and it shares
   * the per-scope fetch cache, so two modals on one page share a flight instead of racing
   * (docs/PERFORMANCE.md).
   */
  $effect(() => {
    void roster.load(effCompany);
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
  /**
   * "Laat schakl de taak invullen" (#327), on a source that could never offer it before (#342).
   * The same two gates the approve dialog uses: the per-task **write** (filling a task in is a
   * task write, whatever route it rode in on) and the AI gate, which keeps the tick off the
   * screen entirely for an org with no provider — off means invisible (#126). And no task
   * picked means no box: "fill in the task" with nothing to fill in is a control that does
   * nothing, ticked by people who reasonably expect it to.
   */
  const canEnrichTask = $derived(
    Boolean(effTask) &&
      canWriteTask(page.data.user, tasks.find((task) => task.value === effTask) ?? null) &&
      aiEnabled(page.data.user, "email_assist"),
  );
  let enrichTask = $state(false);
  let taskCreateOpen = $state(false);
  let taskDraft = $state("");
  /**
   * The task this form made, if it made one — and, with it, whether the save should end with
   * that task open for review beside the message (`TaskReviewDialog`), exactly as the approve
   * in `InteractionMoveDialog` does. The rule is the same one: a task **created here** is
   * unfinished by definition, and a task schakl was just asked to **fill in** is about to change
   * — either way the next act is checking it, so the review opens here rather than closing over
   * the page and leaving it to be found. It used to be wired into the review desk's approve
   * only, so an e-mail picked out of Gmail (or dropped as a `.eml`) onto a new task closed the
   * dialog and nothing more, which was the report on task 80a90bfd.
   *
   * Not when the task is the host's own (`showTask` false — the form is on that task's page):
   * the page is the review, its strip already polls, and a slide-over of the record it is
   * drawn on would be the same task twice.
   */
  let taskCreatedHere = $state("");
  const reviewAfterSave = $derived(
    showTask && Boolean(effTask) && (effTask === taskCreatedHere || (canEnrichTask && enrichTask)),
  );
  /** The task under review after the save — while set, the host is not told to close. */
  let reviewTaskId = $state("");
  let reviewOpen = $state(false);
  let reviewOrigin = $state<{ label: string; title: string; detail?: string | null } | null>(null);
  /** Where the task came from, as the review names it: the picked message, or the file. */
  function originOf(): { label: string; title: string; detail?: string | null } {
    const label = t(
      effTask === taskCreatedHere ? "tasks.review.origin" : "tasks.review.origin_enriched",
    );
    if (gmail && picked) {
      const who = picked.from_name || picked.from_email || "";
      const when = picked.occurred_at ? fmtDateTime(picked.occurred_at) : "";
      return {
        label,
        title: picked.subject || t("interactions.detail_title"),
        detail: [who, when].filter(Boolean).join(" · "),
      };
    }
    return { label, title: filename || t("interactions.eml.title") };
  }
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
          assignees?: { user_id: string }[] | null;
          assignee_user_id?: string | null;
        }
      | undefined;
    if (!created || created.id === handledCreate) return;
    if (created.slot === "eml_contact") {
      handledCreate = created.id;
      // Offers them, adds the chip, and drops the shared per-scope cache so the next form to
      // open knows about them too (#290).
      roster.created(created.id, created.name || qcName || "—");
    } else if (created.slot === "eml_task") {
      handledCreate = created.id;
      // Made here, on this message: the save that follows opens it for review (above).
      taskCreatedHere = created.id;
      if (!tasks.some((task) => task.value === created.id)) {
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
      if (!fTasks.includes(created.id)) fTasks = [...fTasks, created.id];
      if (fTasks[0] === created.id) onTaskPicked(created.id);
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

{#if reviewTaskId}
  <!-- Logged, and the task it was filed onto is open beside this for review. The form is done;
       what is left of this dialog says so, and closes with the review. -->
  <p class="text-sm text-text-muted">{t("interactions.logged_review")}</p>
  {#if skipped}
    <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">
      {tn("interactions.eml.attachments_skipped", skipped)}
    </p>
  {/if}
{:else}
  {#if gmailOffered}
    <!-- Which source, before anything else: the whole rest of the dialog is the same either way,
       so this is the only decision the two paths do not share. Not tabs-as-navigation — the
       form below is one form, and this switches where its message comes from. -->
    <div class="mb-4 flex flex-wrap items-center gap-1">
      <button
        type="button"
        onclick={() => (source = "file")}
        class="rounded-lg px-3 py-1.5 text-sm font-medium {source === 'file'
          ? 'bg-surface text-text ring-1 ring-inset ring-border'
          : 'text-text-muted hover:text-text'}"
      >
        {t("interactions.eml.source_file")}
      </button>
      <button
        type="button"
        onclick={() => (source = "gmail")}
        class="rounded-lg px-3 py-1.5 text-sm font-medium {source === 'gmail'
          ? 'bg-surface text-text ring-1 ring-inset ring-border'
          : 'text-text-muted hover:text-text'}"
      >
        {t("interactions.eml.source_gmail")}
      </button>
    </div>
  {/if}

  {#if gmail}
    <!-- Its own form, and a sibling of the one below rather than a child: HTML has no nested
       forms, and the two really are separate submissions — looking a message up reads, logging
       it writes. A form action rather than a browser fetch, so the flow needs no edge route to
       proxy `/api/v1` and behaves the same in every deployment. -->
    <form
      method="POST"
      action="?/lookupGmailMessage"
      class="mb-4 space-y-2"
      use:enhance={busy.wrap("gmail-lookup", () => async ({ result, update }) => {
        if (result.type === "failure") {
          gmailError = String(result.data?.error ?? "errors.validation");
          lookup = null;
          picked = null;
          return;
        }
        gmailError = "";
        lookup =
          result.type === "success"
            ? ((result.data?.gmailLookup ?? null) as LookupResult | null)
            : null;
        picked = null;
        // `reset: false`: the reference is what the user must correct when nothing came back,
        // and blanking it sends them to Gmail to copy the same link a second time.
        await update({ reset: false });
      })}
    >
      {#if threadId}
        <input type="hidden" name="thread_id" value={threadId} />
        <p class="text-sm font-medium text-text">{t("interactions.gmail.thread_results")}</p>
        <p class="text-xs text-text-muted">{t("interactions.gmail.thread_hint")}</p>
      {:else}
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.gmail.reference")}</span>
          <input
            type="text"
            name="reference"
            bind:value={reference}
            placeholder={t("interactions.gmail.reference_placeholder")}
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
          />
        </label>
        <p class="text-xs text-text-muted">{t("interactions.gmail.reference_hint")}</p>
      {/if}
      <Button type="submit" variant="secondary" loading={busy.is("gmail-lookup")}>
        <Search size={15} aria-hidden="true" />
        {threadId ? t("interactions.gmail.thread_fetch") : t("interactions.gmail.search")}
      </Button>
    </form>

    {#if !threadId}
      <!-- The other way in (#372). A separate form for the same reason the lookup is one: two
         submissions, two sets of inputs, one result list. Kept collapsed because a reference is
         the faster route when you have one — but reachable in one click, because most of the
         time nobody has one, and "go and copy an id out of Gmail" was the whole problem. -->
      <details class="mb-4 rounded-lg border border-border">
        <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-text">
          {t("interactions.gmail.search_toggle")}
        </summary>
        <form
          method="POST"
          action="?/searchGmailMessages"
          class="space-y-2 border-t border-border p-3"
          use:enhance={busy.wrap("gmail-search", () => async ({ result, update }) => {
            if (result.type === "failure") {
              gmailError = String(result.data?.error ?? "errors.validation");
              lookup = null;
              picked = null;
              return;
            }
            gmailError = "";
            lookup =
              result.type === "success"
                ? ((result.data?.gmailLookup ?? null) as LookupResult | null)
                : null;
            picked = null;
            // `reset: false`: the fields describe a search somebody is refining, and blanking
            // them after each attempt makes narrowing a result set impossible.
            await update({ reset: false });
          })}
        >
          <label class="block text-sm">
            <span class="mb-1 block font-medium text-text">
              {t("interactions.gmail.search_participant")}
            </span>
            <input
              type="email"
              name="participant"
              bind:value={searchParticipant}
              placeholder={t("interactions.gmail.search_participant_placeholder")}
              class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
            />
          </label>
          <label class="block text-sm">
            <span class="mb-1 block font-medium text-text">
              {t("interactions.gmail.search_subject")}
            </span>
            <input
              type="text"
              name="subject"
              bind:value={searchSubject}
              class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
            />
          </label>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="block text-sm">
              <span class="mb-1 block font-medium text-text">
                {t("interactions.gmail.search_after")}
              </span>
              <input
                type="date"
                name="after"
                bind:value={searchAfter}
                class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
              />
            </label>
            <label class="block text-sm">
              <span class="mb-1 block font-medium text-text">
                {t("interactions.gmail.search_before")}
              </span>
              <input
                type="date"
                name="before"
                bind:value={searchBefore}
                class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
              />
            </label>
          </div>
          <p class="text-xs text-text-muted">{t("interactions.gmail.search_hint")}</p>
          <Button type="submit" variant="secondary" loading={busy.is("gmail-search")}>
            <Search size={15} aria-hidden="true" />
            {t("interactions.gmail.search_submit")}
          </Button>
        </form>
      </details>
    {/if}

    {#if gmailError}
      <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(gmailError)}</p>
    {/if}
    {#if lookup}
      <div class="mb-4">
        {#if lookup.widened_to_thread}
          <!-- "I pasted one link and got eight messages" is surprising unless it is said. -->
          <p class="mb-2 text-xs text-text-muted">{t("interactions.gmail.widened")}</p>
        {/if}
        <GmailMessagePicker
          messages={lookup.messages ?? []}
          truncated={lookup.truncated ?? false}
          selected={picked?.message_id ?? ""}
          onpick={(message) => {
            picked = message;
            duplicate = false;
            error = "";
          }}
        />
      </div>
    {/if}
  {/if}

  <form
    method="POST"
    action={gmail ? "?/importGmailMessage" : "?/uploadInteractionEml"}
    enctype={gmail ? "application/x-www-form-urlencoded" : "multipart/form-data"}
    class="space-y-4"
    use:enhance={busy.wrap("", () => async ({ result, update }) => {
      if (result.type === "failure") {
        duplicate = Boolean(result.data?.emlDuplicate ?? result.data?.gmailDuplicate);
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      duplicate = false;
      const uploaded = (result.type === "success" ? result.data?.emlUploaded : null) as
        { stored: number; skipped: number } | null | undefined;
      skipped = uploaded?.skipped ?? 0;
      // Decided before `update()` re-renders anything: the review's origin is the message that
      // was just logged, and the picker it came from is about to be replaced by a sentence.
      const review = reviewAfterSave ? effTask : "";
      const origin = review ? originOf() : null;
      await update({ reset: false });
      if (review) {
        // The save that made (or is filling in) a task hands it over: the review opens over
        // this form and the host is told to close only when that review is done.
        reviewOrigin = origin;
        reviewTaskId = review;
        reviewOpen = true;
        return;
      }
      // A skipped attachment is worth a sentence, so the modal stays open to say it.
      if (!skipped) onsaved?.();
    })}
  >
    {#if gmail}
      <input type="hidden" name="message_id" value={picked?.message_id ?? ""} />
    {/if}
    {#each Object.entries(hidden) as [field, value] (field)}
      <input type="hidden" name={field} {value} />
    {/each}
    <!-- Set only after the duplicate warning: the second press is the deliberate one. -->
    <input type="hidden" name="allow_duplicate" value={duplicate ? "1" : "0"} />

    {#if !gmail}
      <!-- A .eml gets here by being dragged out of a mail client, which is the one gesture this
       screen exists for, so the whole block is the drop target. -->
      <div use:filedrop={{ onerror: (key) => (error = key) }}>
        <span class="mb-1 block text-sm font-medium text-text">{t("interactions.eml.file")}</span>
        <label
          class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm text-text-muted hover:border-brand focus-within:border-brand hover:text-brand"
        >
          <Paperclip size={15} aria-hidden="true" />
          {filename || t("interactions.eml.choose")}
          <input
            type="file"
            name="file"
            accept=".eml,message/rfc822"
            required
            class="sr-only"
            onchange={(e) => {
              filename = e.currentTarget.files?.[0]?.name ?? "";
              duplicate = false;
              error = "";
              skipped = 0;
            }}
          />
        </label>
        <span class="ml-2 text-xs text-text-muted">{t("common.drop_hint")}</span>
        <p class="mt-1 text-xs text-text-muted">{t("interactions.eml.hint")}</p>
      </div>
    {/if}

    <div class="grid gap-4 sm:grid-cols-2">
      {#if showCompany}
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
            id="eml-company"
          />
        </label>
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
            id="eml-project"
          />
        </label>
      {/if}
      {#if showTask}
        <div class="block text-sm">
          <span class="mb-1 block font-medium text-text">{t("interactions.field.tasks")}</span>
          <TaskChips
            bind:picked={fTasks}
            items={taskOptions}
            archived={linkSplit.tasks.retired}
            archivedLabel={t("tasks.picker.archived")}
            labels={taskLabels}
            onpick={onTaskPicked}
            oncreate={canCreateTask
              ? (query) => {
                  taskDraft = query;
                  taskCreateOpen = true;
                }
              : undefined}
            id="eml-tasks"
          />
        </div>
      {/if}
      <div class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.contacts")}</span>
        <ContactChips
          {roster}
          id="eml-contacts"
          oncreate={canCreateContact ? (query) => void quickCreateContact(query) : undefined}
        />
      </div>
    </div>

    {#if canEnrichTask}
      <!-- The offer #342 unbundled from the review transition: it belongs to *filing an email
         onto a task*, which is something all three sources do. Off by default — sending a
         client's own words to a model is a decision, not an inheritance. -->
      <label class="flex items-start gap-2 rounded-lg border border-border p-3 text-sm text-text">
        <input
          type="checkbox"
          name="enrich_task"
          value="1"
          bind:checked={enrichTask}
          class="mt-0.5"
        />
        <span>
          {t("interactions.eml.enrich_task")}
          <span class="mt-0.5 block text-xs text-text-muted"
            >{t("interactions.eml.enrich_task_hint")}</span
          >
        </span>
      </label>
    {/if}

    {#if skipped}
      <p class="text-sm text-amber-700 dark:text-amber-400">
        {tn("interactions.eml.attachments_skipped", skipped)}
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
      <Button type="submit" loading={busy.is("")} disabled={busy.active || (gmail && !picked)}>
        <Mail size={15} aria-hidden="true" />
        {duplicate
          ? t("interactions.eml.upload_anyway")
          : gmail
            ? t("interactions.gmail.submit")
            : t("interactions.eml.submit")}
      </Button>
    </div>
  </form>
{/if}

<!-- The task the save just filed onto, open for review beside the message (`reviewAfterSave`).
     The project options are this form's own, already narrowed to the client the task was filed
     under; every way out of the review closes the host with it. -->
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
  assignees={page.data.user?.id ? [{ user_id: page.data.user.id, is_primary: true }] : []}
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
