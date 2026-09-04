<script lang="ts">
  /**
   * The new-task dialog: **the** way a task is made (#391). It began behind a picker's
   * "＋ … toevoegen" (docs/UX.md) and is what `Nieuwe taak` opens too — on `/tasks`, on a
   * client's Taken panel, on the client header and on a project's to-do list — because a task
   * is never written before it has been named and given a client. The placeholder create that
   * used to sit behind the primary buttons (#350's `unnamed` rows) is gone: an abandoned create
   * was a task on somebody's board, and marking it never made it not one.
   *
   * Real fields — title, due date, the client, the employees on it — prefilled with what was
   * typed, posting to the caller's own action: a picker's answers with `inlineCreated` so it
   * auto-selects the new task, the list's redirects to the task in edit mode. The company and
   * project ride along hidden when the caller has them pinned (a client's page, a project's
   * to-do list, the approve dialog's current picks); when the client is *not* pinned the dialog
   * draws its own picker for it, because a task without one is refused (`taskCreateBody`) and
   * a control that is missing is a refusal the user cannot act on.
   *
   * The roster is the same `AssigneePicker` the full task form draws (#375), not a single
   * Combobox. A task created from a pending email is routinely work for two people, and a
   * dialog that can only name one made "assign the pair" a second visit to the task itself —
   * which is exactly the trip the inline-create exists to save. It posts the whole roster in
   * one hidden field, so the caller's action forwards `assignees` and never a lone id.
   *
   * Self-sufficient on its lookups: a host that has the org's clients or colleagues on hand
   * passes them, and one that does not (a client's Taken panel) lets the dialog fetch them the
   * first time it opens — the same lazy read the client's contacts already get, so no page pays
   * for a dialog most opens never see (docs/PERFORMANCE.md).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import {
    companyArchivedLabel,
    type PickerCompany,
    splitCompanyOptions,
  } from "$lib/modules/companies/picker";

  import TaskAssigneePicker from "./TaskAssigneePicker.svelte";

  interface Member {
    user_id: string;
    full_name: string | null;
    email: string | null;
    is_active?: boolean;
  }

  let {
    open = $bindable(false),
    title = "",
    companyId = null,
    projectId = null,
    companies = [],
    members = [],
    assignees = [],
    action = "?/createTask",
    error = null,
    pickerSlot = "task",
  }: {
    open?: boolean;
    /** What was typed in the picker. */
    title?: string;
    /** Pinned by the surface: posted hidden and not asked for. `null` draws the picker. */
    companyId?: string | null;
    projectId?: string | null;
    /** The org's clients, when the host already has them; fetched on open otherwise. */
    companies?: PickerCompany[];
    // `email` is nullable on `/members/lookup` and on `AssigneePicker`, which is what this
    // forwards them to; narrowing it here only made every caller cast.
    members?: Member[];
    /**
     * The roster the picker opens with. `Nieuwe taak` used to assign its creator behind the
     * scenes (#391); it still offers to, as a chip that is visible and removable rather than a
     * decision taken off screen. Empty is the honest default everywhere the surface never
     * assigned anyone.
     */
    assignees?: { user_id: string; is_primary: boolean }[];
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects. */
    pickerSlot?: string;
  } = $props();

  const busy = new InFlight();

  /**
   * The refusal this very submit came back with.
   *
   * `error` (the page's `form?.qcError`) only reaches a dialog whose action lives on the page it
   * is drawn on: SvelteKit applies a *failure* result to `page.form` only for a same-path
   * action, and three of the four callers post to `/tasks?/create` from somewhere else. Reading
   * the result here works for all of them, and a dialog that stays open saying nothing is the
   * worst of the available answers.
   */
  let refusal = $state<string | null>(null);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // The client this task is for: pinned by the host, or picked here. Reset on every open, so
  // a dialog reopened for a second task does not carry the first one's pick.
  let chosenCompany = $state("");
  $effect(() => {
    if (open) chosenCompany = companyId ?? "";
  });

  // The org's clients, for the picker the dialog draws when nothing pinned one. Read once per
  // mount and only when needed: a host with the list on hand passes it, and the common opens —
  // from a client's own page, from a project — never pay for it.
  let fetchedCompanies = $state<PickerCompany[]>([]);
  let companiesLoaded = $state(false);
  $effect(() => {
    if (!open || companyId || companies.length > 0 || companiesLoaded) return;
    companiesLoaded = true;
    void (async () => {
      const response = await fetch("/api/v1/companies?limit=200&offset=0&count=false&sort=name", {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      fetchedCompanies = ((await response.json()).items ?? []) as PickerCompany[];
    })();
  });
  const companyList = $derived(companies.length > 0 ? companies : fetchedCompanies);
  const companyPicker = $derived(splitCompanyOptions(companyList, { selectedId: chosenCompany }));

  // The colleagues, for a host that has none on hand (a panel on the client hub). Same lazy
  // read, same reason.
  let fetchedMembers = $state<Member[]>([]);
  let membersLoaded = $state(false);
  $effect(() => {
    if (!open || members.length > 0 || membersLoaded) return;
    membersLoaded = true;
    void (async () => {
      const response = await fetch("/api/v1/members/lookup", {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      fetchedMembers = (await response.json()) as Member[];
    })();
  });
  const employees = $derived(members.length > 0 ? members : fetchedMembers);

  // The client's contacts (#453), so a task can be *for* the client from the dialog rather than
  // only from the task's own edit mode. Fetched when the dialog has a client — pinned, or just
  // picked — and the same endpoint the task page reads in edit mode. Read-only callers (a
  // client's own portal login) get an empty list and the picker stays employee-only.
  let contacts = $state<{ id: string; name: string }[]>([]);
  let contactsFor = $state<string>("");
  $effect(() => {
    const target = chosenCompany;
    if (!open || !target || target === contactsFor) return;
    void (async () => {
      const response = await fetch(`/api/v1/contacts?limit=200&company_id=${target}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      interface ContactRow {
        id: string;
        first_name: string;
        last_name?: string | null;
      }
      const rows: ContactRow[] = (await response.json()).items ?? [];
      contacts = rows.map((c) => ({
        id: c.id,
        name: [c.first_name, c.last_name].filter(Boolean).join(" "),
      }));
      contactsFor = target;
    })();
  });
</script>

<Modal bind:open title={t("tasks.new")}>
  {#key title + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", ({ formData, cancel }) => {
        refusal = null;
        // A task is a client's. The picker posts through a hidden input, and a hidden control
        // is barred from constraint validation by definition (docs/UX.md, #392's `required`
        // lesson) — so the check is made here, before the round trip, and the sentence lands
        // under the picker rather than as "er ging iets mis" over the form.
        if (!String(formData.get("company_id") ?? "").trim() && !projectId) {
          refusal = "errors.tasks_company_required";
          cancel();
          return;
        }
        // Somebody is always on a task. The roster travels as one hidden field too. A client
        // contact is somebody (#453); a form that drew no picker at all posts no `assignees`
        // and the action assigns the caller.
        const roster = String(formData.get("assignees") ?? "");
        const contact = String(formData.get("assignee_contact_id") ?? "").trim();
        if (roster && !contact && roster.replace(/\s/g, "") === "[]") {
          refusal = "errors.tasks_assignee_required";
          cancel();
          return;
        }
        return ({ result, update }) => {
          // A redirect closes it too: the list's create lands on the new task in edit mode
          // (#391), and a dialog left open would flash over the page being navigated to.
          if (result.type === "success" || result.type === "redirect") open = false;
          else if (result.type === "failure") {
            const data = (result.data ?? {}) as { qcError?: string; error?: string };
            refusal = data.qcError ?? data.error ?? "errors.validation";
          }
          void update({ reset: false });
        };
      })}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={pickerSlot} />
      {#if companyId}<input type="hidden" name="company_id" value={companyId} />{/if}
      {#if projectId}<input type="hidden" name="project_id" value={projectId} />{/if}
      <div>
        <label for="qc-task-title" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.title")}</label
        >
        <input id="qc-task-title" name="title" value={title} required class={inputClass} />
      </div>
      <!-- The client, unless the surface pinned one. Required: a task with no client is on no
           client's page and outside every company horizon, so the dialog asks rather than
           letting one through and finding out on the API. No "Wissen" (`allowEmpty`), since an
           empty pick is the one state the next submit refuses (#392's DateInput lesson). -->
      {#if !companyId}
        <div>
          <label for="qc-task-company" class="mb-1 block text-sm font-medium text-text"
            >{t("tasks.field.company")}</label
          >
          <Combobox
            items={companyPicker.live}
            archived={companyPicker.retired}
            archivedLabel={companyArchivedLabel()}
            name="company_id"
            value={chosenCompany}
            id="qc-task-company"
            allowEmpty={false}
            placeholder={t("tasks.field.company")}
            onselect={(value) => (chosenCompany = value)}
          />
        </div>
      {/if}
      <!-- Required (#392): a task with no deadline is invisible to every urgency screen, so
           the dialog asks rather than letting one through and finding out on the API. -->
      <div>
        <label for="qc-task-due" class="mb-1 block text-sm font-medium text-text"
          >{t("tasks.field.due_date")}</label
        >
        <DateInput name="due_date" id="qc-task-due" required />
      </div>
      <!-- Guarded on the roster, not on the opening list: a picker whose every option sits
           behind the search is still a picker, and hiding it would take the search with it.
           Employees, or — when the task has a client (#273/#453) — one of that client's
           contacts: the same control the task page draws, posting `assignees` and
           `assignee_contact_id` so the caller's body builder never has to guess which. -->
      {#if employees.length > 0 || contacts.length > 0}
        <div>
          <span class="mb-1 block text-sm font-medium text-text">{t("tasks.field.assignees")}</span>
          <TaskAssigneePicker
            {employees}
            {contacts}
            contactsEnabled={!!chosenCompany && contacts.length > 0}
            {assignees}
            id="qc-task-assignee"
          />
        </div>
      {/if}
      {#if refusal ?? error}
        <p class="text-sm text-red-600 dark:text-red-400">{t((refusal ?? error)!)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>
