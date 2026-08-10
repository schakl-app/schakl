/**
 * Client-callable proxy for the tier-2 drill-downs (issue #133).
 *
 * The tab renders its stored trends instantly from `+page.server`, then fetches each drill-down
 * (top pages/queries/campaigns) lazily from here so a slow or failing Google call never blocks
 * the page. The API caches these ~1h and returns a labelled `unavailable` state, never a throw.
 */
import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  const q = event.url.searchParams;
  const company_id = q.get("company_id") ?? "";
  const link_id = q.get("link_id") ?? "";
  const kind = q.get("kind") ?? "";
  // The period token travels verbatim; the API resolves it against the tenant's own calendar
  // (#316), so a drill-down covers exactly the span the tiles above it were computed from.
  const period = q.get("period") || "30d";
  const { data, error } = await apiFor(event).GET(
    "/api/v1/marketing/companies/{company_id}/drilldown",
    { params: { path: { company_id }, query: { link_id, kind, period } } },
  );
  if (error || !data) {
    return json({
      kind,
      rows: [],
      available: false,
      unavailable_reason: "marketing.accounts_error",
    });
  }
  return json(data);
};
