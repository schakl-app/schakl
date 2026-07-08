import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function currentYear(): number {
  return new Date().getUTCFullYear();
}

function parseYear(raw: string | null): number {
  const year = Number(raw);
  return Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : currentYear();
}

export const load: PageServerLoad = async (event) => {
  // Manager surface: approvals + team balances.
  if (!event.locals.user?.canManage) throw redirect(303, "/leave");
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));

  const [pending, yearRequests, members, entitlements, profiles] = await Promise.all([
    api.GET("/api/v1/leave/requests", {
      params: { query: { all_users: true, status: "pending", limit: 100, offset: 0 } },
    }),
    api.GET("/api/v1/leave/requests", {
      params: { query: { all_users: true, year, limit: 200, offset: 0 } },
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
  };
};

export const actions: Actions = {
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
  register: async (event) => {
    const form = await event.request.formData();
    const body = {
      user_id: String(form.get("user_id") ?? "") || null,
      leave_type_id: String(form.get("leave_type_id") ?? ""),
      start_date: String(form.get("start_date") ?? ""),
      end_date: String(form.get("end_date") ?? ""),
      hours: Number(form.get("hours") ?? 0),
      note: String(form.get("note") ?? "").trim() || null,
    };
    if (!body.leave_type_id || !body.start_date || !body.end_date || !body.hours) {
      return fail(400, { error: "errors.required" });
    }
    const { error } = await apiFor(event).POST("/api/v1/leave/requests", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { registered: true };
  },
};
