import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // Manager gate + company lookups live in the /overview layout load.
  const currentYear = new Date().getUTCFullYear();
  const raw = Number(event.url.searchParams.get("year"));
  const year = Number.isInteger(raw) && raw >= 2000 && raw <= 2100 ? raw : currentYear;

  const { data: stats } = await apiFor(event).GET("/api/v1/time/stats/revenue", {
    params: { query: { year } },
  });
  return { stats: stats ?? null, year };
};
