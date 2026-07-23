import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { LEAVE_COLUMNS, LEAVE_TABLE_ID } from "$lib/modules/leave/columns";
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
  const api = apiFor(event);
  const year = parseYear(event.url.searchParams.get("year"));

  // The saved layout decides the sort the *server* applies. It comes from the (app) layout load,
  // which does not rerun on year navigation (docs/PERFORMANCE.md); the URL wins over the saved
  // default so a sorted list stays shareable.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, LEAVE_TABLE_ID);
  const resolved = resolveColumns(LEAVE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  // Types + contract hours come from the /leave layout load; only the year data changes here.
  // #265: the combined per-group balances — statutory + extra-statutory vacation roll up into one
  // "Vakantieverlof" figure, with each pot's expiry alongside.
  const [groups, requests] = await Promise.all([
    api.GET("/api/v1/leave/balance/groups", { params: { query: { year } } }),
    api.GET("/api/v1/leave/requests", {
      params: { query: { year, limit: 100, offset: 0, sort } },
    }),
  ]);
  return {
    year,
    currentYear: currentYear(),
    groups: groups.data ?? [],
    requests: requests.data?.items ?? [],
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, LEAVE_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  create: async (event) => {
    const form = await event.request.formData();
    const body = requestBody(form);
    if (!body.leave_type_id || !body.start_date || !body.end_date) {
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

  /** Bulk cancel of own requests. Per-id, skipping what the API refuses (a decided row, a
   *  past-locked one) — partial success is reported, never silent. */
  bulkCancel: async (event) => {
    const form = await event.request.formData();
    const ids = String(form.get("ids") ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
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

  /** Own recurring free days (#107): the API restricts a member to self-service types. */
  saveRecurring: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const typeId = String(form.get("leave_type_id") ?? "");
    const anchor = String(form.get("anchor_date") ?? "");
    const interval = Number(form.get("interval_weeks") ?? 0);
    if (!userId || !typeId || !anchor || !interval) {
      return fail(400, { error: "errors.required" });
    }
    const { data, error } = await apiFor(event).POST("/api/v1/leave/recurring", {
      body: {
        user_id: userId,
        leave_type_id: typeId,
        anchor_date: anchor,
        interval_weeks: interval,
        start_time: String(form.get("start_time") ?? "").trim() || null,
        end_time: String(form.get("end_time") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // `recurringAdded` separates the add from the toggle/delete: the add closes the modal
    // (#271), so its confirmation is the page's to render, not the modal's.
    return { recurringSaved: true, recurringAdded: true, recurringGenerated: data?.generated ?? 0 };
  },

  toggleRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).PATCH("/api/v1/leave/recurring/{recurring_id}", {
      params: { path: { recurring_id: id } },
      body: { active: form.get("active") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return {
      recurringSaved: true,
      recurringAdded: false,
      recurringGenerated: data?.generated ?? 0,
    };
  },

  deleteRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/leave/recurring/{recurring_id}", {
        params: { path: { recurring_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { recurringSaved: true, recurringAdded: false, recurringGenerated: 0 };
  },
};
