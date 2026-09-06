import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { burnFilterToken } from "$lib/modules/projects/burn-groups";
import {
  OVERVIEW_PROJECTS_TABLE_ID,
  PROJECT_REPORT_COLUMNS,
} from "$lib/modules/projects/report-columns";
import { PROJECT_STATUS_ALL } from "$lib/modules/projects/status";

import type { Actions, PageServerLoad } from "./$types";

/** Projects that can still burn a budget: running or paused. `?status=all` widens it. */
const REPORT_WORKING_SET = "active,on_hold";

export const load: PageServerLoad = async (event) => {
  const user = event.locals.user;
  const enabled = event.locals.theme?.enabledModules ?? [];
  // Two modules' reads on one row (report-columns.ts), so two modules' keys (#310).
  if (
    !can(user, "time.report.read") ||
    !enabled.includes("projects") ||
    !can(user, "projects.project.read")
  ) {
    throw redirect(303, "/overview");
  }
  const api = apiFor(event);
  const q = event.url.searchParams;
  const statusFilter = q.get("status") ?? "";
  const status = statusFilter === PROJECT_STATUS_ALL ? undefined : REPORT_WORKING_SET;
  // A burn band (#437) — the tiles above the table open these, and the API filters on the
  // enriched burn so the count a tile prints is the count the list shows.
  const burn = burnFilterToken(q.get("burn"));

  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, OVERVIEW_PROJECTS_TABLE_ID);
  const resolved = resolveColumns(PROJECT_REPORT_COLUMNS, pref);
  const sort = q.get("sort") ?? resolved.sort ?? undefined;
  const paging = resolvePaging(event.url, pref);

  const [projects, stats, budgets] = await Promise.all([
    api.GET("/api/v1/projects", {
      params: {
        query: {
          limit: paging.limit,
          offset: paging.offset,
          sort,
          hours: true,
          status,
          burn,
        },
      },
    }),
    // One grouped statement over every project with time, joined onto the page by id — never a
    // `/cost` call per row (docs/PERFORMANCE.md).
    api.GET("/api/v1/time/stats/projects"),
    // The whole set's burn bands, so a tile says "4 over budget" over the list that shows 4.
    api.GET("/api/v1/projects/dashboard-budgets", { params: { query: { limit: 1 } } }),
  ]);
  const byProject = new Map((stats.data?.rows ?? []).map((row) => [row.project_id, row]));
  const rows = (projects.data?.items ?? []).map((project) => ({
    ...project,
    time: byProject.get(project.id) ?? null,
  }));
  const allTime = stats.data?.rows ?? [];
  return {
    rows,
    total: projects.data?.total ?? 0,
    paging,
    statusFilter,
    burn: burn ?? "",
    budgets: budgets.data ?? null,
    totals: {
      billable_amount: allTime.reduce((sum, r) => sum + r.billable_amount, 0),
      billable_minutes: allTime.reduce((sum, r) => sum + r.billable_minutes, 0),
      invoiced_minutes: allTime.reduce((sum, r) => sum + r.invoiced_minutes, 0),
      unrated_minutes: allTime.reduce((sum, r) => sum + r.unrated_minutes, 0),
    },
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Persist this manager's column layout (personal, in-view — docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, OVERVIEW_PROJECTS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
};
