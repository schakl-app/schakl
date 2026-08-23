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
import type { MarketingSource } from "$lib/modules/marketing/types";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  const source = (event.url.searchParams.get("source") ?? "") as MarketingSource;
  // Forwarded only when the caller sent one: `website_id` is what a site-key source (Rank Math)
  // names its credential by, and every other source ignores it. Empty is not `null` at the API
  // boundary — an empty string would 422 as a malformed UUID rather than reading as "no site".
  const websiteId = event.url.searchParams.get("website_id") || null;
  // Skips the API's short account cache. The picker offers it once a credential exists, because
  // somebody who has just created the brand they came here to link must not be handed a
  // ten-minute-old list with no control that disagrees with it (#435).
  const refresh = event.url.searchParams.get("refresh") === "1";
  const { data, error } = await apiFor(event).GET("/api/v1/marketing/accounts", {
    params: { query: { source, website_id: websiteId, refresh } },
  });
  if (error || !data) {
    return json({ source, connected: false, accounts: [], error: "marketing.accounts_error" });
  }
  return json(data);
};
