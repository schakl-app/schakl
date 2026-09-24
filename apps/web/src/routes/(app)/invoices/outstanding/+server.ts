import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/** What a client still has to be invoiced for — hours, agreement periods, domain renewals —
 *  feeding the line editor's three section pickers.
 *
 *  A thin proxy: the API enforces the permission and the company horizon, so no permission
 *  simply means no picker rather than an error the editor would have to render. One call for
 *  all three sections, because the dialog opens on all three (docs/PERFORMANCE.md). */
export const GET: RequestHandler = async (event) => {
  const company_id = event.url.searchParams.get("company_id") ?? "";
  const empty = { hours: { entries: [], total_minutes: 0, hourly_rate: null }, subscriptions: [], domains: [] };
  if (!company_id) return json(empty);
  const { data, error } = await apiFor(event).GET("/api/v1/invoicing/outstanding", {
    params: { query: { company_id } },
  });
  if (error || !data) return json(empty);
  return json(data);
};
