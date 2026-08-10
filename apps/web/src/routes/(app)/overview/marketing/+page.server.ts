import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  MARKETING_OVERVIEW_COLUMNS,
  MARKETING_OVERVIEW_TABLE_ID,
} from "$lib/modules/marketing/columns";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "marketing.overview.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const q = event.url.searchParams;
  const range = q.get("range") ?? "30d";

  // Manager gate is in the /overview layout; prefs come from the app layout (both via parent()).
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, MARKETING_OVERVIEW_TABLE_ID);
  const resolved = resolveColumns(MARKETING_OVERVIEW_COLUMNS, pref);
  // The server sorts (one query over the stored table); the grid only asks for an ordering.
  const sort = q.get("sort") ?? resolved.sort ?? undefined;

  const { data } = await api.GET("/api/v1/marketing/overview", {
    params: { query: { period: range, sort } },
  });

  return {
    overview: data ?? { range_days: 30, rows: [], total: 0 },
    range,
    // Managing links (and thus the key-events toggle) is admin config; the grid renders the
    // switch only for those who hold it, and the API re-checks regardless.
    canManage: can(event.locals.user, "marketing.link.manage"),
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Persist this manager's column layout (personal, in-view — docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, MARKETING_OVERVIEW_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** Show/hide GA4 key events / conversions for one client, straight from the grid (#134). */
  toggleKeyEvents: async (event) => {
    if (!can(event.locals.user, "marketing.link.manage"))
      return fail(403, { error: "errors.forbidden" });
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { error: "errors.required" });
    const show_key_events = String(form.get("show_key_events") ?? "") === "true";
    const { error } = await apiFor(event).PUT("/api/v1/marketing/companies/{company_id}/settings", {
      params: { path: { company_id } },
      body: { show_key_events },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { keyEventsToggled: true };
  },
};
