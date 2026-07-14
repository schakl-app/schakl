import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  MARKETING_OVERVIEW_COLUMNS,
  MARKETING_OVERVIEW_TABLE_ID,
} from "$lib/modules/marketing/columns";

import type { Actions, PageServerLoad } from "./$types";

const PRESET_DAYS: Record<string, number> = { "30d": 30, "90d": 90, quarter: 90, yoy: 365 };

function rangeToDays(range: string): number {
  if (range === "month") return Math.max(1, new Date().getDate() - 1);
  return PRESET_DAYS[range] ?? 30;
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "marketing.report.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const q = event.url.searchParams;
  const range = q.get("range") ?? "30d";
  const range_days = rangeToDays(range);

  // Manager gate is in the /overview layout; prefs come from the app layout (both via parent()).
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, MARKETING_OVERVIEW_TABLE_ID);
  const resolved = resolveColumns(MARKETING_OVERVIEW_COLUMNS, pref);
  // The server sorts (one query over the stored table); the grid only asks for an ordering.
  const sort = q.get("sort") ?? resolved.sort ?? undefined;

  const { data } = await api.GET("/api/v1/marketing/overview", {
    params: { query: { range_days, sort } },
  });

  return {
    overview: data ?? { range_days, rows: [], total: 0 },
    range,
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
};
