/**
 * Client-callable proxy for the account pickers (issue #132).
 *
 * The picker options come from Google (slow, per-connection) so they load lazily when the user
 * opens the picker, not on every company-page render. The browser fetches this endpoint; it
 * forwards to the API through the request-scoped typed client (cookie + tenant host attached),
 * which serves from its short Redis cache and returns a teaching state on not-connected/no-scope.
 */
import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  const source = (event.url.searchParams.get("source") ?? "") as "ga4" | "gsc" | "gads";
  const { data, error } = await apiFor(event).GET("/api/v1/marketing/accounts", {
    params: { query: { source } },
  });
  if (error || !data) {
    return json({ source, connected: false, accounts: [], error: "marketing.accounts_error" });
  }
  return json(data);
};
