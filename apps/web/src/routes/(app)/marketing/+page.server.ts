import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

const PRESET_DAYS: Record<string, number> = { "30d": 30, "90d": 90, quarter: 90, yoy: 365 };

function rangeToDays(range: string): number {
  if (range === "month") return Math.max(1, new Date().getDate() - 1);
  return PRESET_DAYS[range] ?? 30;
}

export const load: PageServerLoad = async (event) => {
  // The API enforces the permission too; redirect a member who lacks it rather than showing a
  // bare page (the nav item is already hidden for them).
  if (!can(event.locals.user, "marketing.metrics.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const companyId = event.url.searchParams.get("company") || "";
  const range = event.url.searchParams.get("range") ?? "30d";
  // Website filter: "" = everything, "client" = client-level links only, else a website id.
  // Filtering happens client-side — the metrics payload already carries every link.
  const website = event.url.searchParams.get("website") || "";
  const range_days = rangeToDays(range);

  // The client list feeds the picker (name-only); the metrics load only when a client is picked.
  const companiesP = api.GET("/api/v1/companies", {
    params: { query: { limit: 200, offset: 0, count: false } },
  });
  const metricsP = companyId
    ? api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
        params: { path: { company_id: companyId }, query: { range_days } },
      })
    : null;
  const [companies, metrics] = await Promise.all([companiesP, metricsP]);

  return {
    companies: companies.data?.items ?? [],
    companyId,
    metrics: metrics?.data ?? null,
    range,
    rangeDays: range_days,
    website,
  };
};
