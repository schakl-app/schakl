/** Shared client-side lookups for the interaction link pickers (#147, #183, #168-followup). */

export interface LinkOption {
  value: string;
  label: string;
}
export interface ProjectOption extends LinkOption {
  company_id: string | null;
}
export interface TaskOption extends LinkOption {
  project_id: string | null;
  company_id: string | null;
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
    companies: (companiesPage.items ?? []).map((c: { id: string; name: string }) => ({
      value: c.id,
      label: c.name,
    })),
    projects: (projectsPage.items ?? []).map(
      (p: { id: string; name: string; company_id?: string | null }) => ({
        value: p.id,
        label: p.name,
        company_id: p.company_id ?? null,
      }),
    ),
    tasks: (tasksPage.items ?? []).map(
      (task: {
        id: string;
        title: string;
        project_id?: string | null;
        company_id?: string | null;
      }) => ({
        value: task.id,
        label: task.title,
        project_id: task.project_id ?? null,
        company_id: task.company_id ?? null,
      }),
    ),
  };
}
