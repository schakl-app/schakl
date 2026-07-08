import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

function monthStartIso(): string {
  return new Date().toISOString().slice(0, 8) + "01";
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export const load: PageServerLoad = async (event) => {
  // Manager gate + member lookups live in the /overview layout load.
  const q = event.url.searchParams;
  const filters = {
    date_from: q.get("date_from") ?? monthStartIso(),
    date_to: q.get("date_to") ?? todayIso(),
  };
  const { data: stats } = await apiFor(event).GET("/api/v1/time/stats/productivity", {
    params: { query: { date_from: filters.date_from, date_to: filters.date_to } },
  });
  return { stats: stats ?? null, filters };
};
