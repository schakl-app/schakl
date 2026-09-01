/**
 * The body every "＋ nieuwe taak" surface posts (#391).
 *
 * Create-then-edit (#230, docs/UX.md Principle 3) is still the shape — a task's definition is
 * edited on exactly one surface, so there is no second create form to keep in step — but the
 * *name* is asked for before the row exists. It used to be a placeholder ("Naamloze taak") the
 * server made up, marked `unnamed` (#350) so a screen could at least say so, and one click on
 * `Nieuwe taak` was enough to leave a real task on the board, in the client's Taken panel and in
 * the export. Marking those rows was the right mitigation for a display problem and never
 * addressed the one underneath: an abandoned create is still work nobody asked for.
 *
 * So the *dialog* builds a **named** row or refuses ({@link taskCreateBody}) — that is what a
 * picker's inline ＋ needs, because it has to hand an id back to the control that opened it and
 * may not navigate away.
 *
 * What that reasoning does **not** justify is putting a dialog in front of `Nieuwe taak` itself.
 * A create there is not a picker's side errand: the user is going to the task anyway, and a
 * modal asking three of its twenty fields is a form in front of a form — the second one being
 * the surface where every field, including those three, is actually edited. So the primary
 * create paths are create-then-edit again ({@link taskPlaceholderBody}): one click writes a
 * placeholder row and lands the user in edit mode over it, which is the shape #392 already
 * wrote down for this case ("create-then-edit writes the org's own today over a placeholder row
 * it is about to drop the user into") and #350 already gave a name to (`unnamed`, so an
 * abandoned one is findable and reads as unnamed in the reader's own language).
 *
 * Kept out of the actions that call it so the rule can be asserted without a browser
 * (`apps/web/tests/unit/task-create.test.ts`); no `$lib` alias, for the same reason.
 */
import { type Assignee, parseAssignees } from "../../core/assignees.ts";

export interface TaskCreateBody {
  title: string;
  /** Only the placeholder path sets it (#350): nobody typed this title. */
  unnamed?: boolean;
  /** Required (#392) — see {@link taskCreateBody}. Never `null`, and never invented here. */
  due_date: string;
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
 * `null` when the caller supplied no title **or no deadline** — the action turns that into
 * `errors.required` rather than inventing one. The API refuses both too (`TaskCreate.title` is
 * `min_length=1` and `due_date` is required since #392); this is the half that can name the
 * field before the round trip.
 *
 * The deadline joined the title for the same reason the title was moved in front of the row
 * (#391): a task with no `due_date` is absent from `?due=overdue`, from the Agenda's deadline
 * feed and from both dashboards' overdue counts, so it is not merely unscheduled — it is
 * invisible to every screen that is about time, which is what the team means by *niet kan
 * worden overgeslagen*. Both dialogs mark the field `required`, so this is the backstop rather
 * than the thing a person meets. A **deadline is still not a calendar booking**: planning the
 * work into the agenda stays optional and setting one never implies the other.
 *
 * @param opts.projectId  pinned by the surface (a project's to-do list), overriding the form.
 * @param opts.fallbackAssigneeUserId who to assign when the dialog rendered no roster picker at
 *   all — an org with nobody to offer. A rendered picker's answer is always honoured, `[]`
 *   included: "nobody" is a decision, and `undefined` is the absence of one (`parseAssignees`).
 */
export function taskCreateBody(
  form: PostedFields,
  opts: { projectId?: string | null; fallbackAssigneeUserId?: string | null } = {},
): TaskCreateBody | null {
  const title = String(form.get("title") ?? "").trim();
  const dueDate = String(form.get("due_date") ?? "").trim();
  if (!title || !dueDate) return null;

  const assignees = parseAssignees(form.get("assignees"));
  // The client's contact (#453): `TaskAssigneePicker` posts it beside an explicitly empty
  // roster, and the API refuses the pair — so a contact wins outright and no employee rides.
  const contactId = String(form.get("assignee_contact_id") ?? "").trim();
  const companyId = String(form.get("company_id") ?? "").trim();
  const projectId = (opts.projectId ?? String(form.get("project_id") ?? "")).trim();

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

/**
 * The row create-then-edit writes *before* anybody has been asked anything — one click on
 * `Nieuwe taak`, then the detail page in edit mode over it.
 *
 * Three of its four fields are decided here rather than by a form, and each has a reason:
 *
 * - **the title** is a placeholder the caller resolves in the *reader's* locale and `unnamed`
 *   says so (#350). Before that flag an abandoned row was indistinguishable from real work and
 *   the placeholder was frozen in the creator's language, so one org held both "Naamloze taak"
 *   and "Untitled task" and neither was findable as "the ones nobody named". The flag clears
 *   itself the moment a real title is saved (`TaskService`), which is one keystroke away.
 * - **the deadline** is the org's own today (`orgToday()`, never the server's UTC clock — §8),
 *   which is exactly the default #392 wrote down for this path. It is a real, editable value on
 *   the field the user is about to be looking at, not a `NULL` that would take the task out of
 *   every urgency screen.
 * - **the assignee** is whoever pressed the button, the way this button has always worked.
 *
 * `unnamed` is what makes the trade-off answerable rather than invisible: an abandoned create is
 * a row, and `?unnamed=1` is the list of them.
 */
export function taskPlaceholderBody(opts: {
  /** `t("tasks.untitled")`, resolved by the caller — it is the one with the locale. */
  title: string;
  /** `orgToday()`, resolved by the caller — it is the one inside the tenant's timezone store. */
  today: string;
  companyId?: string | null;
  projectId?: string | null;
  assigneeUserId?: string | null;
}): TaskCreateBody {
  return {
    title: opts.title,
    unnamed: true,
    due_date: opts.today,
    company_id: opts.companyId || null,
    project_id: opts.projectId || null,
    // Status is omitted so the API assigns the org's default status (issue #62).
    priority: "normal",
    assignee_user_id: opts.assigneeUserId ?? null,
    // New tasks don't demand a closing contact moment; toggled later on the task page (#157).
    requires_interaction: false,
    visible_to_client: false,
  };
}
