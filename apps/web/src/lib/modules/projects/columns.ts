/**
 * The columns a project list can show (#24, #25).
 *
 * Plain metadata, no Svelte — the server `load` reads it to decide whether the budget burn-down
 * is visible and therefore worth computing. `sortKey` mirrors the API's allow-list
 * (`apps/api/app/modules/projects/service.py::SORTABLE`); a column with no `sortKey` has a quiet
 * header because the server genuinely cannot order by it.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const PROJECTS_TABLE_ID = "projects";

/** The derived budget column; its presence is what makes the list pay for the aggregate. */
export const HOURS_COLUMN = "hours";
/** The columns a client-portal login is never drawn (#449): the API blanks these fields. */
export const STAFF_COLUMNS = new Set([HOURS_COLUMN, "budget_hours"]);

// Every non-primary column states a width, because the table is `table-fixed`: an undeclared
// one is not "as wide as it needs", it is an equal share of the slack the primary column is
// supposed to absorb — so two bare date columns end up as wide as the project name. The four
// shown by default sum to ~680px, which on an ordinary laptop leaves the name the rest.
export const PROJECT_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "projects.field.name", sortKey: "name", primary: true, width: 240 },
  { key: "company", labelKey: "projects.field.company", defaultVisible: true, width: 200 },
  {
    key: "status",
    labelKey: "projects.field.status",
    sortKey: "status",
    defaultVisible: true,
    width: 130,
  },
  // Sorts by the *primary* assignee's display name; the API orders by it, not by user id.
  {
    key: "assignees",
    labelKey: "projects.field.assignees",
    sortKey: "assignee",
    defaultVisible: true,
    width: 150,
  },
  {
    key: HOURS_COLUMN,
    labelKey: "table.column.hours_burn",
    align: "right",
    defaultVisible: true,
    width: 200,
  },
  {
    key: "budget_hours",
    labelKey: "projects.field.budget_hours",
    sortKey: "budget_hours",
    align: "right",
    width: 140,
  },
  {
    key: "start_date",
    labelKey: "projects.field.start_date",
    sortKey: "start_date",
    align: "right",
    width: 120,
  },
  {
    key: "end_date",
    labelKey: "projects.field.end_date",
    sortKey: "end_date",
    align: "right",
    width: 120,
  },
];
