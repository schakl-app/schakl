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
 * So this builds a **named** row or refuses. `unnamed` is deliberately absent from the body:
 * the column and the `?unnamed=1` filter stay (existing rows need them, and the field is part of
 * a public API contract), but nothing in the web app produces one any more.
 *
 * Kept out of the actions that call it so the rule can be asserted without a browser
 * (`apps/web/tests/unit/task-create.test.ts`); no `$lib` alias, for the same reason.
 */
import { type Assignee, parseAssignees } from "../../core/assignees.ts";

export interface TaskCreateBody {
  title: string;
  due_date: string | null;
  company_id: string | null;
  project_id: string | null;
  priority: "normal";
  requires_interaction: boolean;
  visible_to_client: boolean;
  assignees?: Assignee[];
  assignee_user_id?: string | null;
}

/** Just enough of `FormData` to build a body from — so a test needs no DOM. */
export interface PostedFields {
  get(name: string): FormDataEntryValue | null;
}

/**
 * `null` when the caller supplied no title — the action turns that into `errors.required`
 * rather than inventing one. The API refuses an empty title too (`TaskCreate.title` is
 * `min_length=1`); this is the half that can name the field before the round trip.
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
  if (!title) return null;

  const assignees = parseAssignees(form.get("assignees"));
  const dueDate = String(form.get("due_date") ?? "").trim();
  const companyId = String(form.get("company_id") ?? "").trim();
  const projectId = (opts.projectId ?? String(form.get("project_id") ?? "")).trim();

  return {
    title,
    due_date: dueDate || null,
    company_id: companyId || null,
    project_id: projectId || null,
    // Status is omitted so the API assigns the org's default status (issue #62).
    priority: "normal",
    ...(assignees !== undefined
      ? { assignees }
      : { assignee_user_id: opts.fallbackAssigneeUserId ?? null }),
    // New tasks don't demand a closing contact moment; toggled later on the task page (#157).
    requires_interaction: false,
    visible_to_client: false,
  };
}
