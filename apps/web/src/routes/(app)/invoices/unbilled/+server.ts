import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/** Live preview for the "invoice unbilled hours" dialog: the same set from-time will bill.
 *  A thin proxy — the API enforces the permission. */
export const GET: RequestHandler = async (event) => {
  const company_id = event.url.searchParams.get("company_id") ?? "";
  const until = event.url.searchParams.get("until") || undefined;
  if (!company_id) return json({ entries: [], total_minutes: 0, hourly_rate: null });
  const { data, error } = await apiFor(event).GET("/api/v1/invoicing/unbilled", {
    params: { query: { company_id, until } },
  });
  if (error || !data) return json({ entries: [], total_minutes: 0, hourly_rate: null });
  return json(data);
};
