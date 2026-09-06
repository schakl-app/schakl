import type { ColumnMeta } from "$lib/core/table/columns";

/**
 * Overzicht → Projecten: every budgeted project with its burn, its logged hours and what those
 * hours are worth. Two sources, one row: the budget columns are the projects API's (`?hours=true`,
 * the burn over the budget's own period), the hour and money columns are the time module's
 * all-time aggregate (`/time/stats/projects`). Only the columns the projects API can order by
 * carry a `sortKey` — sorting happens on the server, and a header that silently did nothing
 * would be worse than an honest one (`core/table/columns.ts`).
 */
export const OVERVIEW_PROJECTS_TABLE_ID = "overview_projects";

export const PROJECT_REPORT_COLUMNS: ColumnMeta[] = [
  {
    key: "project",
    labelKey: "overview.projects.column.project",
    sortKey: "name",
    primary: true,
    width: 240,
  },
  { key: "client", labelKey: "overview.projects.column.client", width: 180, defaultVisible: true },
  {
    key: "status",
    labelKey: "overview.projects.column.status",
    sortKey: "status",
    width: 110,
    defaultVisible: true,
  },
  {
    key: "budget",
    labelKey: "overview.projects.column.budget",
    sortKey: "budget_hours",
    width: 220,
    defaultVisible: true,
  },
  {
    key: "hours",
    labelKey: "overview.projects.column.hours",
    align: "right",
    width: 100,
    defaultVisible: true,
  },
  {
    key: "billable",
    labelKey: "overview.projects.column.billable",
    align: "right",
    width: 110,
    defaultVisible: true,
  },
  {
    key: "invoiced",
    labelKey: "overview.projects.column.invoiced",
    align: "right",
    width: 110,
    defaultVisible: true,
  },
  {
    key: "value",
    labelKey: "overview.projects.column.value",
    align: "right",
    width: 130,
    defaultVisible: true,
  },
  {
    key: "budget_amount",
    labelKey: "overview.projects.column.budget_amount",
    align: "right",
    width: 120,
  },
];
