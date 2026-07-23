import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { LEAVE_TEAM_COLUMNS, LEAVE_TEAM_TABLE_ID } from "$lib/modules/leave/columns";
// The employment editors (work schedule, contracts, recurring free days) are the same shared
// surface as Instellingen → Gebruikers, so the roster ⋯ menu reuses its actions verbatim.
import { employmentActions } from "$lib/modules/leave/employment.server";
import { requestBody } from "$lib/modules/leave/request";
import { defaultSchedule } from "$lib/modules/leave/schedule";

import type { Actions, PageServerLoad } from "./$types";

function currentYear(): number {
  return new Date().getUTCFullYear();
}

function parseYear(raw: string | null): number {
  const year = Number(raw);
  return Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : currentYear();
}

/** Selected ids: repeated `ids` fields from the bulk bar, or one comma-joined value from a
 *  ConfirmDialog's flat `fields` record. */
function _bulkIds(form: FormData): string[] {
  return form
    .getAll("ids")
    .flatMap((v) => String(v).split(","))
    .map((s) => s.trim())
    .filter(Boolean);
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

  // Editing a member's employment data (schedule/contracts/recurring) from the roster ⋯ menu is
  // gated on `leave.profile.manage`, distinct from the `leave.request.approve` that opens this
  // page — an approver who can't manage schedules gets no menu and none of these three calls.
  const manage = can(event.locals.user, "leave.profile.manage");

  const [pending, yearRequests, members, entitlements, profiles, contracts, recurring, settings] =
    await Promise.all([
      api.GET("/api/v1/leave/requests", {
        params: { query: { all_users: true, status: "pending", limit: 100, offset: 0 } },
      }),
      api.GET("/api/v1/leave/requests", {
        params: { query: { all_users: true, year, limit: 200, offset: 0, sort } },
      }),
      api.GET("/api/v1/members/lookup"),
      api.GET("/api/v1/leave/entitlements", { params: { query: { year } } }),
      api.GET("/api/v1/leave/profiles"),
      // Employment editors — the whole roster in one call each, like Instellingen → Gebruikers.
      manage
        ? api.GET("/api/v1/leave/contracts", { params: { query: { all_users: true } } })
        : Promise.resolve({ data: null }),
      manage ? api.GET("/api/v1/leave/recurring") : Promise.resolve({ data: null }),
      manage ? api.GET("/api/v1/leave/settings") : Promise.resolve({ data: null }),
    ]);

  return {
    year,
    currentYear: currentYear(),
    pending: pending.data?.items ?? [],
    yearRequests: yearRequests.data?.items ?? [],
    members: members.data ?? [],
    entitlements: entitlements.data ?? [],
    profiles: profiles.data ?? [],
    // Employment editors, only when the caller may manage them.
    manageEmployment: manage,
    contracts: contracts.data ?? [],
    recurring: recurring.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  // Work schedule, contracts and recurring free days — the same handlers Instellingen →
  // Gebruikers uses, so the roster ⋯ menu behaves identically (employment.server.ts).
  ...employmentActions,

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

  // Edit a member's request from the team table (#106). The API recomputes hours (#48) and
  // decides whether the change re-enters approval (#72).
  update: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/leave/requests/{request_id}", {
      params: { path: { request_id: id } },
      body: requestBody(form),
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  /**
   * Bulk approve/reject (#leave bulk). Sequential decide calls per id — the API's guards run
   * per request, so one already-decided row (409) is *skipped*, never a reason to abort the
   * rest. Reports processed vs skipped; silent partial success would read as "all done".
   */
  bulkDecide: async (event) => {
    const form = await event.request.formData();
    const ids = _bulkIds(form);
    if (ids.length === 0) return fail(400, { error: "errors.required" });
    const approved = form.get("approved") === "true";
    const api = apiFor(event);
    let done = 0;
    for (const id of ids) {
      const { error } = await api.POST("/api/v1/leave/requests/{request_id}/decide", {
        params: { path: { request_id: id } },
        body: { approved, note: null },
      });
      if (!error) done += 1;
    }
    return { bulkDone: done, bulkSkipped: ids.length - done };
  },

  bulkCancel: async (event) => {
    const form = await event.request.formData();
    const ids = _bulkIds(form);
    if (ids.length === 0) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    let done = 0;
    for (const id of ids) {
      const { error } = await api.POST("/api/v1/leave/requests/{request_id}/cancel", {
        params: { path: { request_id: id } },
      });
      if (!error) done += 1;
    }
    return { bulkDone: done, bulkSkipped: ids.length - done };
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
