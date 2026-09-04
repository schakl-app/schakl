/**
 * The body every "＋ nieuwe taak" surface posts (#391).
 *
 * Create-then-edit (#230, docs/UX.md Principle 3) is still the shape — a task's definition is
 * edited on exactly one surface, so there is no second create form to keep in step — but what
 * *identifies* the task is asked for before the row exists: its name, its deadline, who is on
 * it, and whose it is. There used to be a second, placeholder-writing body beside this one
 * (`taskPlaceholderBody`, #350): one click on `Nieuwe taak` wrote a row titled "Naamloze taak",
 * marked `unnamed` so a list could italicise it and a filter could gather it, and dropped the
 * user into edit mode over it. Every abandoned create was a task on somebody's board, in the
 * client's Taken panel and in the export, and the flag only ever said so. That path is gone —
 * the owner's decision, stated once: **a task is never created unnamed, and never without a
 * client** — so there is exactly one body builder and it refuses rather than inventing.
 *
 * Kept out of the actions that call it so the rule can be asserted without a browser
 * (`apps/web/tests/unit/task-create.test.ts`); no `$lib` alias, for the same reason.
 */
import { type Assignee, parseAssignees } from "../../core/assignees.ts";

export interface TaskCreateBody {
  title: string;
  /** Required (#392) — see {@link taskCreateBody}. Never `null`, and never invented here. */
  due_date: string;
  /**
   * The client. `null` only when the surface pinned a *project* instead, in which case the API
   * takes the client off the project (a project has exactly one); otherwise the builder refuses.
   */
  company_id: string | null;
  project_id: string | null;
  priority: "normal";
  requires_interaction: boolean;
  visible_to_client: boolean;
  assignees?: Assignee[];
  assignee_user_id?: string | null;
  /** A client contact instead of a roster (#273/#453); the API makes the task client-visible. */
  assignee_contact_id?: string;
}

/** Just enough of `FormData` to build a body from — so a test needs no DOM. */
export interface PostedFields {
  get(name: string): FormDataEntryValue | null;
}

/**
 * `null` when the caller supplied no title, no deadline, nobody to hold the task, **or no
 * client** — the action turns that into a refusal rather than inventing one. The API refuses
 * all four too (`TaskCreate.title` is `min_length=1` and trimmed, `due_date` is required since
 * #392, `company_id` since the rule above); this is the half that can name the field before
 * the round trip.
 *
 * The deadline joined the title for the same reason the title was moved in front of the row
 * (#391): a task with no `due_date` is absent from `?due=overdue`, from the Agenda's deadline
 * feed and from both dashboards' overdue counts, so it is not merely unscheduled — it is
 * invisible to every screen that is about time, which is what the team means by *niet kan
 * worden overgeslagen*. A **deadline is still not a calendar booking**: planning the work into
 * the agenda stays optional and setting one never implies the other.
 *
 * **Somebody is always on the task**, and that is the third refusal. A rendered picker's
 * answer is honoured — a roster, or a client contact — except the one answer that names nobody:
 * `[]` with no contact is refused, not sent, because a task with no one on it is on no one's
 * board, in no one's "mijn taken" and in no one's nudges, which is #392's invisibility one
 * column over. The dialog says so before the round trip (`TaskQuickCreate` cancels the submit
 * and prints the sentence under the picker); this is the backstop.
 *
 * **The task is a client's**, and that is the fourth. A task with no client is on no client's
 * page, in no client's export, on no report and outside every company horizon — the one place
 * the agency's own work cannot be, since an agency that wants a to-do list for itself is a
 * client of itself. The client comes pinned by the surface (`opts.companyId`: a client's page,
 * its Taken panel), or from the dialog's own picker, or — the one indirection — off a pinned
 * project (`opts.projectId`), which the API resolves because a project has exactly one client.
 * Nothing else stands in.
 *
 * @param opts.companyId  pinned by the surface (a client's page), overriding the form.
 * @param opts.projectId  pinned by the surface (a project's to-do list), overriding the form.
 * @param opts.fallbackAssigneeUserId who to assign when the dialog rendered no roster picker at
 *   all — an org with nobody to offer, so the form carries no `assignees` field (`undefined`,
 *   the absence of a decision — `parseAssignees`). Every action passes the caller here, which is
 *   what `Nieuwe taak` has always done; without it a picker-less form is refused too.
 */
export function taskCreateBody(
  form: PostedFields,
  opts: {
    companyId?: string | null;
    projectId?: string | null;
    fallbackAssigneeUserId?: string | null;
  } = {},
): TaskCreateBody | null {
  const title = String(form.get("title") ?? "").trim();
  const dueDate = String(form.get("due_date") ?? "").trim();
  if (!title || !dueDate) return null;

  const assignees = parseAssignees(form.get("assignees"));
  // The client's contact (#453): `TaskAssigneePicker` posts it beside an explicitly empty
  // roster, and the API refuses the pair — so a contact wins outright and no employee rides.
  const contactId = String(form.get("assignee_contact_id") ?? "").trim();
  // A rendered picker that answers "nobody" is refused; a form with no picker falls back to
  // the caller, and only a caller-less one is refused as well.
  const nobodyChosen = !contactId && assignees !== undefined && assignees.length === 0;
  const nobodyToFallTo = !contactId && assignees === undefined && !opts.fallbackAssigneeUserId;
  if (nobodyChosen || nobodyToFallTo) return null;
  const companyId = (opts.companyId ?? String(form.get("company_id") ?? "")).trim();
  const projectId = (opts.projectId ?? String(form.get("project_id") ?? "")).trim();
  if (!companyId && !projectId) return null;

  return {
    title,
    due_date: dueDate,
    company_id: companyId || null,
    project_id: projectId || null,
    // Status is omitted so the API assigns the org's default status (issue #62).
    priority: "normal",
    ...(contactId
      ? { assignee_contact_id: contactId, assignees: [] }
      : assignees !== undefined
        ? { assignees }
        : { assignee_user_id: opts.fallbackAssigneeUserId ?? null }),
    // New tasks don't demand a closing contact moment; toggled later on the task page (#157).
    requires_interaction: false,
    visible_to_client: false,
  };
}
