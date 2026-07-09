import { fail } from "@sveltejs/kit";

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
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));
  // Types + contract hours come from the /leave layout load; only the year data changes here.
  const [balance, requests] = await Promise.all([
    api.GET("/api/v1/leave/balance", { params: { query: { year } } }),
    api.GET("/api/v1/leave/requests", {
      params: { query: { year, limit: 100, offset: 0 } },
    }),
  ]);
  return {
    year,
    currentYear: currentYear(),
    balances: balance.data ?? [],
    requests: requests.data?.items ?? [],
  };
};

function requestBody(form: FormData) {
  return {
    leave_type_id: String(form.get("leave_type_id") ?? ""),
    start_date: String(form.get("start_date") ?? ""),
    end_date: String(form.get("end_date") ?? ""),
    hours: Number(form.get("hours") ?? 0),
    note: String(form.get("note") ?? "").trim() || null,
  };
}

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const body = requestBody(form);
    if (!body.leave_type_id || !body.start_date || !body.end_date || !body.hours) {
      return fail(400, { error: "errors.required" });
    }
    const { error } = await apiFor(event).POST("/api/v1/leave/requests", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

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
};
