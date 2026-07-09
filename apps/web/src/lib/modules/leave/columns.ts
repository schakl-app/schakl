/**
 * The columns the two leave lists can show (#40) — a person's own requests, and the team's.
 *
 * They are different lists with different identities (mine is a period; the team's is a person),
 * so they get different table ids and their own saved layouts.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/leave/service.py::SORTABLE`).
 * **Type has no sort key**: a leave type's label is per-locale tenant data in a JSONB column, so
 * the server cannot order by what the user actually reads, and a header that claims to sort and
 * doesn't is worse than a quiet one (docs/UX.md).
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const LEAVE_TABLE_ID = "leave";
export const LEAVE_TEAM_TABLE_ID = "leave-team";

/** Verlof → my own requests. */
export const LEAVE_COLUMNS: ColumnMeta[] = [
  {
    key: "period",
    labelKey: "leave.requests.period",
    sortKey: "start_date",
    primary: true,
    width: 220,
  },
  { key: "type", labelKey: "leave.form.type", defaultVisible: true },
  {
    key: "hours",
    labelKey: "leave.form.hours",
    sortKey: "hours",
    align: "right",
    defaultVisible: true,
  },
  { key: "days", labelKey: "leave.requests.days", align: "right" },
  { key: "status", labelKey: "leave.requests.status", sortKey: "status", defaultVisible: true },
];

/** Verlof → Team: the year's requests, whoever made them. */
export const LEAVE_TEAM_COLUMNS: ColumnMeta[] = [
  {
    key: "employee",
    labelKey: "leave.team.member",
    // Orders by display name, never by user id — a list sorted by a person must read that way.
    sortKey: "employee",
    primary: true,
    width: 200,
  },
  {
    key: "period",
    labelKey: "leave.requests.period",
    sortKey: "start_date",
    defaultVisible: true,
  },
  { key: "type", labelKey: "leave.form.type", defaultVisible: true },
  {
    key: "hours",
    labelKey: "leave.form.hours",
    sortKey: "hours",
    align: "right",
    defaultVisible: true,
  },
  { key: "status", labelKey: "leave.requests.status", sortKey: "status", defaultVisible: true },
];
