import { error, redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

/** Preset → trailing-day count. "month" is days-so-far this month; the API clamps to ≥ 1. */
const PRESET_DAYS: Record<string, number> = { "30d": 30, "90d": 90, quarter: 90, yoy: 365 };

function rangeToDays(range: string): number {
  if (range === "month") return Math.max(1, new Date().getDate() - 1);
  return PRESET_DAYS[range] ?? 30;
}

export const load: PageServerLoad = async (event) => {
  // The API enforces the permission; redirect a member who lacks it back to the client page
  // rather than showing an empty tab.
  if (!can(event.locals.user, "marketing.metrics.read")) {
    throw redirect(303, `/companies/${event.params.id}`);
  }
  const api = apiFor(event);
  const company_id = event.params.id;
  const range = event.url.searchParams.get("range") ?? "30d";
  const range_days = rangeToDays(range);

  // Both read our database (zero Google); the drill-downs load lazily client-side afterwards.
  const [company, metrics] = await Promise.all([
    api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } }),
    api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
      params: { path: { company_id }, query: { range_days } },
    }),
  ]);
  if (!company.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    company: company.data,
    metrics: metrics.data ?? null,
    range,
    rangeDays: range_days,
  };
};
