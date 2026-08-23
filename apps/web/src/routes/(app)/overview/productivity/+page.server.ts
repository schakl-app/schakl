import { apiFor } from "$lib/core/session";
import { orgToday } from "$lib/core/today";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // Manager gate + member lookups live in the /overview layout load.
  const q = event.url.searchParams;
  const filters = {
    // The tenant's month and the tenant's today (§8) — not the Node container's, whose `TZ`
    // is UTC in the shipped image and so was a day out all day for anyone east of it.
    date_from: q.get("date_from") ?? orgToday().slice(0, 8) + "01",
    date_to: q.get("date_to") ?? orgToday(),
  };
  const { data: stats } = await apiFor(event).GET("/api/v1/time/stats/productivity", {
    params: { query: { date_from: filters.date_from, date_to: filters.date_to } },
  });
  return { stats: stats ?? null, filters };
};
