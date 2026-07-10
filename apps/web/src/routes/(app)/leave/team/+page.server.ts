import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { LEAVE_TEAM_COLUMNS, LEAVE_TEAM_TABLE_ID } from "$lib/modules/leave/columns";
import { requestBody } from "$lib/modules/leave/request";

import type { Actions, PageServerLoad } from "./$types";

function currentYear(): number {
  return new Date().getUTCFullYear();
}

function parseYear(raw: string | null): number {
  const year = Number(raw);
  return Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : currentYear();
}

export const load: PageServerLoad = async (event) => {
  // The approver surface: pending decisions + everyone's balances.
  if (!can(event.locals.user, "leave.request.approve")) throw redirect(303, "/leave");
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));

  // Only the year table is sortable; the pending approvals are a queue, not a list.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, LEAVE_TEAM_TABLE_ID);
  const resolved = resolveColumns(LEAVE_TEAM_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  const [pending, yearRequests, members, entitlements, profiles] = await Promise.all([
    api.GET("/api/v1/leave/requests", {
      params: { query: { all_users: true, status: "pending", limit: 100, offset: 0 } },
    }),
    api.GET("/api/v1/leave/requests", {
      params: { query: { all_users: true, year, limit: 200, offset: 0, sort } },
    }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/leave/entitlements", { params: { query: { year } } }),
    api.GET("/api/v1/leave/profiles"),
  ]);

  return {
    year,
    currentYear: currentYear(),
    pending: pending.data?.items ?? [],
    yearRequests: yearRequests.data?.items ?? [],
    members: members.data ?? [],
    entitlements: entitlements.data ?? [],
    profiles: profiles.data ?? [],
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Persist this manager's column layout for the team table. Personal, in-view (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, LEAVE_TEAM_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  decide: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/leave/requests/{request_id}/decide", {
      params: { path: { request_id: id } },
      body: {
        approved: form.get("approved") === "true",
        note: String(form.get("note") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { decided: true };
  },

  cancel: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/leave/requests/{request_id}/cancel", {
      params: { path: { request_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { cancelled: true };
  },

  // Register leave on someone's behalf (e.g. a sick call) — API enforces manager role.
  // `hours` is not posted: the server computes it from that employee's schedule (#48).
  register: async (event) => {
    const form = await event.request.formData();
    const body = {
      ...requestBody(form),
      user_id: String(form.get("user_id") ?? "") || null,
    };
    if (!body.leave_type_id || !body.start_date || !body.end_date) {
      return fail(400, { error: "errors.required" });
    }
    const { error } = await apiFor(event).POST("/api/v1/leave/requests", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { registered: true };
  },
};
