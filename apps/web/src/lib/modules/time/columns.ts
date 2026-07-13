/**
 * The columns the Overzicht → Uren report can show (#42).
 *
 * This is the widest list in the app and the one people export from, so configurable columns pay
 * off most here — and several are hidden by default, because the report is already dense.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/time/service.py::SORTABLE`).
 * Client, project, task and employee sort by **name**, never by the foreign key behind the name:
 * the API resolves each through a correlated subquery, so the list orders the way it reads.
 *
 * **`status` carries no `sortKey`.** The pill is derived from three columns (billable × approved
 * × invoiced); no single `ORDER BY` reproduces it, and a header that claims to sort and doesn't
 * is worse than a quiet one (docs/UX.md). Sort by `approved_at` or `invoiced_at` instead — both
 * are offered as their own columns.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const TIME_REPORT_TABLE_ID = "time-report";

export const TIME_REPORT_COLUMNS: ColumnMeta[] = [
  // The report's identity column: an entry is a moment. It has no detail page, so no `rowHref` —
  // the ⋯ menu is how you open one.
  { key: "date", labelKey: "time.field.date", sortKey: "date", primary: true, width: 150 },
  {
    key: "employee",
    labelKey: "time.overview.employee",
    sortKey: "employee",
    defaultVisible: true,
  },
  { key: "company", labelKey: "time.field.company", sortKey: "company", defaultVisible: true },
  { key: "project", labelKey: "time.field.project", sortKey: "project", defaultVisible: true },
  { key: "task", labelKey: "time.field.task", sortKey: "task" },
  {
    key: "description",
    labelKey: "time.field.description",
    sortKey: "description",
    defaultVisible: true,
    width: 260,
  },
  // Tenant-defined type (#176). Label is tenant data, so the server can't order by what the
  // column prints — no sortKey (docs/UX.md).
  { key: "entry_type", labelKey: "time.field.entry_type", width: 150 },
  // Footer: the API's total for the whole filtered set, never a sum of the page (#37).
  {
    key: "minutes",
    labelKey: "time.field.duration",
    sortKey: "minutes",
    align: "right",
    defaultVisible: true,
    width: 120,
  },
  {
    key: "billable",
    labelKey: "time.billable",
    sortKey: "billable",
    align: "right",
    defaultVisible: true,
    width: 120,
  },
  { key: "status", labelKey: "time.overview.column.status", defaultVisible: true, width: 150 },
  { key: "approver", labelKey: "time.overview.column.approver", sortKey: "approver" },
  {
    key: "invoiced_at",
    labelKey: "time.overview.invoiced",
    sortKey: "invoiced_at",
    align: "right",
  },
];
