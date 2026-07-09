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

export const PROJECT_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "projects.field.name", sortKey: "name", primary: true, width: 240 },
  { key: "company", labelKey: "projects.field.company", defaultVisible: true },
  { key: "status", labelKey: "projects.field.status", sortKey: "status", defaultVisible: true },
  { key: "assignees", labelKey: "projects.field.assignees", defaultVisible: true },
  {
    key: HOURS_COLUMN,
    labelKey: "table.column.available_hours",
    align: "right",
    defaultVisible: true,
    width: 200,
  },
  {
    key: "budget_hours",
    labelKey: "projects.field.budget_hours",
    sortKey: "budget_hours",
    align: "right",
  },
  { key: "hourly_rate", labelKey: "projects.field.hourly_rate", align: "right" },
  {
    key: "start_date",
    labelKey: "projects.field.start_date",
    sortKey: "start_date",
    align: "right",
  },
  { key: "end_date", labelKey: "projects.field.end_date", sortKey: "end_date", align: "right" },
];
