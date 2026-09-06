import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { orgToday } from "$lib/core/today";
import { isEmployeePeriod, presetRange } from "$lib/modules/time/periods";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "time.report.read")) throw redirect(303, "/overview");
  const q = event.url.searchParams;
  // The tenant's calendar (§8), never the Node container's — whose `TZ` is UTC in the shipped
  // image and so was a day out all day for anyone east of it.
  const today = orgToday();
  // Explicit dates win over a preset: that is how the Overzicht landing page names a whole year
  // and how the two date fields keep working. Neither given means this month.
  const explicit = Boolean(q.get("date_from") || q.get("date_to"));
  const requested = q.get("period");
  const preset = isEmployeePeriod(requested) ? requested : "month";
  const [presetFrom, presetTo] = presetRange(preset, today);
  const filters = {
    date_from: q.get("date_from") || presetFrom,
    date_to: q.get("date_to") || presetTo,
    // Which pill is lit: a preset, or none when the dates were typed or linked in.
    period: explicit ? "" : preset,
  };
  const { data: stats } = await apiFor(event).GET("/api/v1/time/stats/productivity", {
    params: { query: { date_from: filters.date_from, date_to: filters.date_to } },
  });
  return { stats: stats ?? null, filters };
};
