/**
 * The columns the task board can show (#41).
 *
 * Plain metadata, no Svelte: the page's server `load` reads this to decide the sort it asks the
 * API for, and the component reads the same list to render headers and cells.
 *
 * `TaskRow` is a *composite* row — toggle, title, label chips, checklist and comment counts,
 * allocated time, a red overdue date, assignee initials — and migrating it meant deciding, per
 * badge, whether it becomes a column or stays welded into the title cell. Only the complete
 * toggle stayed: it is not information about the task, it is the way you finish one, and it has
 * to sit next to the title the way it always has. Everything else became a column the user can
 * turn off. `TaskRow` itself lives on: it is this table's mobile row, and it still serves the
 * project detail page and the dashboard widgets.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/tasks/service.py::SORTABLE`).
 * **There is no status column**, though the API can sort by status: the board *groups* by status,
 * so a status sort would reorder rows inside sections that are already status-pure — a control
 * that visibly does nothing. Sorting a grouped table orders within each section (#38).
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const TASKS_TABLE_ID = "tasks";

/** The board's sections, in workflow order — not alphabetical, and no sort may disturb them. */
export const TASK_GROUPS = ["open", "in_progress", "done"] as const;

/** Done starts folded away, as it did before the migration. A saved layout overrides this. */
export const TASK_GROUPS_COLLAPSED_BY_DEFAULT = ["done"];

export const TASK_COLUMNS: ColumnMeta[] = [
  { key: "title", labelKey: "tasks.field.title", sortKey: "title", primary: true, width: 360 },
  { key: "labels", labelKey: "tasks.field.labels", defaultVisible: true },
  // Orders by the employee's display name; the API never orders by the assignee's user id.
  { key: "assignee", labelKey: "tasks.field.assignee", sortKey: "assignee", defaultVisible: true },
  // Ascending is least-urgent-first, so `-priority` floats the fires — the API ranks the
  // vocabulary rather than sorting its spelling.
  { key: "priority", labelKey: "tasks.field.priority", sortKey: "priority", defaultVisible: true },
  {
    key: "due_date",
    labelKey: "tasks.field.due_date",
    sortKey: "due_date",
    align: "right",
    defaultVisible: true,
  },
  { key: "checklist", labelKey: "tasks.field.checklist", align: "right" },
  { key: "comments", labelKey: "tasks.field.comments", align: "right" },
  { key: "allocated", labelKey: "tasks.field.allocated", align: "right" },
  { key: "project", labelKey: "tasks.field.project" },
  { key: "company", labelKey: "tasks.field.company" },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
