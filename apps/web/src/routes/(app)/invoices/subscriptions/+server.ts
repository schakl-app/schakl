import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/** The client's active agreements for the line editor's "＋ abonnement" pick. A thin proxy
 *  — the API enforces the permission; no permission just means no picker. */
export const GET: RequestHandler = async (event) => {
  const company_id = event.url.searchParams.get("company_id") ?? "";
  if (!company_id) return json([]);
  const { data, error } = await apiFor(event).GET("/api/v1/invoicing/billable-subscriptions", {
    params: { query: { company_id } },
  });
  if (error || !data) return json([]);
  return json(data);
};
