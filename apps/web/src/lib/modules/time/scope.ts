/**
 * What an hour entry is *about* — client, project, task — and how picking one of the three
 * answers the other two.
 *
 * The entry form has always had the three pickers and only half a cascade: picking a project
 * filled the client, and nothing else moved. So logging against a task meant naming the same
 * work three times, and the two mistakes that made were both silent. Picking a task left the
 * client and project blank, and the entry was saved unattached to either — which is a row that
 * never reaches the project's budget, never reaches the client's invoice, and looks perfectly
 * normal on the timesheet. And picking a client left a project from *another* client sitting in
 * the field, because the option had been chosen while the list was still org-wide; the picker
 * stopped offering it and kept showing it.
 *
 * One rule, stated once, in three directions:
 *
 * 1. **A pick fills what it implies.** A task knows its project and its client; a project knows
 *    its client. Whatever the picked row can answer, it answers.
 * 2. **A pick clears what it contradicts.** Choosing a client drops a project belonging to a
 *    different one, and the task with it. Never the reverse: filling in *more* detail is not a
 *    reason to throw away context.
 * 3. **Attached to nothing means attached to everything.** A task with no project, a project
 *    with no client — those belong to no project and no client *in particular*, so they are
 *    never contradicted and never cleared. This is the rule the task and project pickers
 *    already follow for what they *offer*; it has to be the same rule for what they *keep*, or
 *    the form clears a row the picker below it is still listing.
 *
 * Only an explicit pick cascades. Clearing a field is not a pick and moves nothing: the client,
 * project and task are the context an afternoon of entries is logged *within*, and emptying the
 * task box is not a request to forget which client you are working for.
 *
 * A task carries no client of its own when it hangs off a project (`Task.company_id` is null
 * there and the API resolves the client through the project), so "whose task is this?" is one
 * question with two places to look. `scopeIndex` is the only place that coalesce lives — the
 * cascade below and the task picker's own narrowing both read it, or the dropdown and the field
 * end up with two answers about the same task.
 *
 * Kept out of `EntryForm.svelte` so `tests/unit/time-scope.test.ts` can hold it to these rules:
 * which of them is wrong is not a thing you can see by opening a dropdown.
 */

export interface ScopeProject {
  id: string;
  company_id?: string | null;
}

export interface ScopeTask {
  id: string;
  company_id?: string | null;
  project_id?: string | null;
}

/** The three fields, as the form holds them: `""` is "not picked". */
export interface Scope {
  companyId: string;
  projectId: string;
  taskId: string;
}

export interface ScopeIndex {
  /** The client a project belongs to, `""` when it has none or the lookup does not hold it. */
  companyOfProject: (projectId: string | null | undefined) => string;
  /** The client a task belongs to: its own, else its project's. `""` when neither says. */
  companyOfTask: (task: ScopeTask | undefined) => string;
  /** The lookup row for a task id, `undefined` when the lookup does not hold it. */
  task: (taskId: string | null | undefined) => ScopeTask | undefined;
}

/**
 * The lookups, indexed once. The form rebuilds this when its lookups change, not per keystroke:
 * a linear scan per picker render is fine at ten rows and is the shape that quietly stops being
 * fine at the lookup's 200 (docs/PERFORMANCE.md).
 */
export function scopeIndex(
  projects: readonly ScopeProject[],
  tasks: readonly ScopeTask[],
): ScopeIndex {
  const projectCompany = new Map(projects.map((project) => [project.id, project.company_id ?? ""]));
  const taskById = new Map(tasks.map((task) => [task.id, task]));
  const companyOfProject = (projectId: string | null | undefined): string =>
    projectId ? (projectCompany.get(projectId) ?? "") : "";
  return {
    companyOfProject,
    companyOfTask: (task) => (task ? (task.company_id ?? companyOfProject(task.project_id)) : ""),
    task: (taskId) => (taskId ? taskById.get(taskId) : undefined),
  };
}

/** Picking a task fills the project it sits on and the client that project is for. */
export function pickTask(taskId: string, current: Scope, index: ScopeIndex): Scope {
  const next: Scope = { ...current, taskId };
  const task = index.task(taskId);
  if (!task) return next;
  if (task.project_id) next.projectId = task.project_id;
  const companyId = index.companyOfTask(task);
  if (companyId) next.companyId = companyId;
  return next;
}

/** Picking a project fills its client and drops a task that belongs to a different project. */
export function pickProject(projectId: string, current: Scope, index: ScopeIndex): Scope {
  const next: Scope = { ...current, projectId };
  const companyId = index.companyOfProject(projectId);
  if (companyId) next.companyId = companyId;
  const task = index.task(current.taskId);
  if (projectId && task?.project_id && task.project_id !== projectId) next.taskId = "";
  return next;
}

/** Picking a client drops the project and task that belong to a different one. */
export function pickCompany(companyId: string, current: Scope, index: ScopeIndex): Scope {
  const next: Scope = { ...current, companyId };
  // No client picked narrows nothing, so it contradicts nothing.
  if (!companyId) return next;
  const projectCompany = index.companyOfProject(current.projectId);
  if (projectCompany && projectCompany !== companyId) next.projectId = "";
  const taskCompany = index.companyOfTask(index.task(current.taskId));
  if (taskCompany && taskCompany !== companyId) next.taskId = "";
  return next;
}
