/** Shared client-side lookups for the interaction link pickers (#147, #183, #168-followup). */
import { fmtPeriod } from "$lib/core/format";
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
  /** The deadline — what tells one occurrence of a repeating task from the next. */
  due_date?: string | null;
  /**
   * `?collapse_series=true` (`TaskListItem.series_pending`): the future occurrences this row
   * stands for, on the series' current one; `null` on every other row. A row carrying a number
   * is the one the picker draws with a chevron.
   */
  series_pending?: number | null;
  /**
   * An occurrence reached by unfolding its series (`loadSeriesOptions`). The host keeps it in
   * its list so the cascade can answer for it (which project, which client, whose) — but it is
   * never offered as a row of its own: that is the fold the list asked the API for.
   */
  nested?: boolean;
}

/** The list row the task lookups read, mapped once for every caller. */
export function toTaskOption(task: {
  id: string;
  title: string;
  project_id?: string | null;
  company_id?: string | null;
  assignees?: { user_id: string }[] | null;
  assignee_user_id?: string | null;
  completed_at?: string | null;
  due_date?: string | null;
  series_pending?: number | null;
}): TaskOption {
  return {
    value: task.id,
    label: task.title,
    project_id: task.project_id ?? null,
    company_id: task.company_id ?? null,
    assignees: (task.assignees ?? []).map((entry) => ({ user_id: entry.user_id })),
    assignee_user_id: task.assignee_user_id ?? null,
    completed_at: task.completed_at ?? null,
    due_date: task.due_date ?? null,
    series_pending: task.series_pending ?? null,
  };
}

/**
 * The task lookup every picker here reads: two hundred rows, title order, no aggregates — and
 * **folded**: a repeating task lays a year of occurrences out, and twelve "Nieuwsbrief" rows in
 * a picker of two hundred are eleven rows the other tasks cannot be found past. The API answers
 * the series' current occurrence with the rest counted onto it; `loadSeriesOptions` is how the
 * picker reaches them when somebody means November's.
 */
export const TASK_LOOKUP_QUERY = "limit=200&count=false&meta=false&sort=title&collapse_series=true";

/**
 * The rest of a series, for the picker's unfold: every *unfinished* occurrence other than the
 * one that stands for them, soonest first. Finished ones are not here because they were never
 * folded — the lookup already lists them behind the search as finished tasks.
 */
export async function loadSeriesOptions(taskId: string): Promise<TaskOption[]> {
  const response = await fetch(
    `/api/v1/tasks?series_id=${taskId}&limit=200&count=false&meta=false&sort=due_date`,
    { headers: { accept: "application/json" } },
  );
  if (!response.ok) return [];
  const page = await response.json();
  return (page.items ?? [])
    .filter(
      (row: { id: string; completed_at?: string | null }) => row.id !== taskId && !row.completed_at,
    )
    .map((row: Parameters<typeof toTaskOption>[0]) => ({ ...toTaskOption(row), nested: true }));
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
    get(`/api/v1/tasks?${TASK_LOOKUP_QUERY}${taskScope}`),
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
    tasks: (tasksPage.items ?? []).map(toTaskOption),
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
 *
 * A repeating task is one row here: its current occurrence, saying what it stands for ("↻ 21 okt
 * · nog 11 gepland") and unfoldable to the rest (`Combobox.onexpand`). The occurrences a host
 * has already unfolded (`nested`) are left out of both buckets — they are reached under their
 * parent, never beside it, or the fold would undo itself the first time somebody opened one.
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
      tasks
        .filter((task) => !task.nested)
        .map((task) => ({
          value: task.value,
          label: task.label,
          // One synthetic key, because the picker's question is binary and the row already
          // answers it. Naming the *status* here would mean fetching the vocabulary to
          // translate it.
          status: task.completed_at ? "done" : "open",
          hint: task.series_pending
            ? t("tasks.picker.series_hint", {
                date: task.due_date ? fmtPeriod(task.due_date) : "",
                count: task.series_pending,
              })
            : undefined,
          expandable: Boolean(task.series_pending),
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
