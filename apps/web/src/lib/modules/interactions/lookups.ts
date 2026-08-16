/** Shared client-side lookups for the interaction link pickers (#147, #183, #168-followup). */
import { t } from "$lib/core/i18n";
import { splitLifecycle, type LifecycleSplit } from "$lib/core/picker";
import { splitCompanyOptions } from "$lib/modules/companies/picker";
import { splitProjectOptions } from "$lib/modules/projects/picker";

export interface LinkOption {
  value: string;
  label: string;
  /**
   * The row's lifecycle status, carried so the pickers can tell a live client or project from
   * one that is over (`$lib/core/picker`). It rides the list response already, so it costs
   * nothing; a lookup that predates it, or an option rebuilt from an edited row, leaves it
   * absent — which reads as "unknown" and keeps the option on offer.
   */
  status?: string | null;
}
export interface ProjectOption extends LinkOption {
  company_id: string | null;
}
export interface TaskOption extends LinkOption {
  project_id: string | null;
  company_id: string | null;
  /**
   * Who the task is assigned to — because "sluit deze taak" is a task write, and
   * `tasks.task.write:own` means *assignee*. Without it the checkbox rendered for a member on
   * every colleague's task and the close came back refused. It rides the list response
   * already (`TaskListItem`, `meta=false` and all), so carrying it costs nothing.
   *
   * The **roster**, not the starred one: `:own` is satisfied by any assignee
   * (`caller_may_write_task`), so a task shared by two people must offer the close to both.
   */
  assignees: { user_id: string }[];
  /** The primary, mirrored by the API — kept beside the roster for callers that read it. */
  assignee_user_id: string | null;
  /**
   * Set once the task reached a finished status — the tenant's own vocabulary stamps it (#62),
   * so this is the one field that answers "is this over?" without fetching the status list.
   */
  completed_at?: string | null;
}

/**
 * Companies / projects / tasks for the company→project→task cascade, loaded on demand (never
 * on page render — a rarely opened form/dialog must not tax every load with three lookups,
 * docs/PERFORMANCE.md). Lean: no counts, no task aggregates.
 *
 * A host-pinned dimension scopes the task fetch (#222) — the deeper link wins, like the task
 * page's own reference lookup — so a company page's picker never offers another client's
 * tasks. Companies and projects stay broad: their pickers may point anywhere.
 */
export async function loadLinkLookups(
  scope: { companyId?: string | null; projectId?: string | null } = {},
): Promise<{
  companies: LinkOption[];
  projects: ProjectOption[];
  tasks: TaskOption[];
}> {
  const get = async (url: string) => {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    return response.ok ? response.json() : { items: [] };
  };
  const taskScope = scope.projectId
    ? `&project_id=${scope.projectId}`
    : scope.companyId
      ? `&company_id=${scope.companyId}`
      : "";
  const [companiesPage, projectsPage, tasksPage] = await Promise.all([
    get("/api/v1/companies?limit=200&count=false&sort=name"),
    get("/api/v1/projects?limit=200&count=false"),
    get(`/api/v1/tasks?limit=200&count=false&meta=false&sort=title${taskScope}`),
  ]);
  return {
    companies: (companiesPage.items ?? []).map(
      (c: { id: string; name: string; status?: string | null }) => ({
        value: c.id,
        label: c.name,
        status: c.status ?? null,
      }),
    ),
    projects: (projectsPage.items ?? []).map(
      (p: { id: string; name: string; company_id?: string | null; status?: string | null }) => ({
        value: p.id,
        label: p.name,
        company_id: p.company_id ?? null,
        status: p.status ?? null,
      }),
    ),
    tasks: (tasksPage.items ?? []).map(
      (task: {
        id: string;
        title: string;
        project_id?: string | null;
        company_id?: string | null;
        assignees?: { user_id: string }[] | null;
        assignee_user_id?: string | null;
        completed_at?: string | null;
      }) => ({
        value: task.id,
        label: task.title,
        project_id: task.project_id ?? null,
        company_id: task.company_id ?? null,
        assignees: (task.assignees ?? []).map((entry) => ({ user_id: entry.user_id })),
        assignee_user_id: task.assignee_user_id ?? null,
        completed_at: task.completed_at ?? null,
      }),
    ),
  };
}

/**
 * The three link pickers, split into what is still going on and what is over.
 *
 * A moment is logged against work that is live far more often than against work that has ended,
 * so the opening lists are the live rows — and the ended ones are not removed, because filing
 * last month's email under the project it actually belonged to is an ordinary thing to do. They
 * move behind `Combobox`'s search and say which status they are in (`$lib/core/picker`).
 *
 * Tasks are judged on `completed_at` rather than on a status key: which statuses mean finished
 * is the tenant's own vocabulary (#62), and the stamp is the answer the API has already applied
 * it to — so no second lookup is needed to draw a dropdown.
 */
export function splitLinkOptions(
  {
    companies,
    projects,
    tasks,
  }: { companies: LinkOption[]; projects: ProjectOption[]; tasks: TaskOption[] },
  selected: { companyId?: string; projectId?: string; taskId?: string } = {},
): {
  companies: LifecycleSplit;
  projects: LifecycleSplit;
  tasks: LifecycleSplit;
} {
  return {
    companies: splitCompanyOptions(
      companies.map((c) => ({ id: c.value, name: c.label, status: c.status })),
      { selectedId: selected.companyId },
    ),
    projects: splitProjectOptions(
      projects.map((p) => ({
        id: p.value,
        name: p.label,
        status: p.status,
        company_id: p.company_id,
      })),
      { selectedId: selected.projectId },
    ),
    tasks: splitLifecycle(
      tasks.map((task) => ({
        value: task.value,
        label: task.label,
        // One synthetic key, because the picker's question is binary and the row already answers
        // it. Naming the *status* here would mean fetching the vocabulary to translate it.
        status: task.completed_at ? "done" : "open",
      })),
      {
        retired: ["done"],
        quiet: ["open"],
        statusLabel: () => t("tasks.picker.finished"),
        selectedId: selected.taskId,
      },
    ),
  };
}
