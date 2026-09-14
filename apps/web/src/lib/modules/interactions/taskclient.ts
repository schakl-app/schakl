/**
 * Whose task an option is, resolved from lookups the picker's host already holds.
 *
 * While no client is picked, the task list of a review or link dialog spans every client the
 * agency has, and "Maandrapportage nakijken" on four clients' work is four identical rows
 * (`DashboardTaskItem.company_name`'s rule, one picker over). A task's client is its own, or —
 * when it only names a project — that project's, which is the same two-column anchor `TaskRow`
 * reads. Resolved in the browser from the companies and projects lookups rather than carried
 * on the task list: `meta=false` exists so a 200-row lookup stays a name per row.
 *
 * Dependency-free on purpose, so node's runner can pin it (`tests/unit`).
 */

export interface TaskClientSources {
  companies: { value: string; label: string }[];
  projects: { value: string; company_id: string | null }[];
}

/** A resolver from the two lookups: the task's client name, or `undefined` when nothing names one. */
export function taskClientName({
  companies,
  projects,
}: TaskClientSources): (task: {
  company_id: string | null;
  project_id: string | null;
}) => string | undefined {
  const names = new Map(companies.map((company) => [company.value, company.label]));
  const projectClient = new Map(projects.map((project) => [project.value, project.company_id]));
  return (task) => {
    const companyId =
      task.company_id ?? (task.project_id ? (projectClient.get(task.project_id) ?? null) : null);
    return companyId ? names.get(companyId) : undefined;
  };
}
