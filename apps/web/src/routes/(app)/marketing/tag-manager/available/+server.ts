/**
 * Client-callable proxy for the container search.
 *
 * The browser cannot reach `/api/v1/...` directly — there is no vite proxy in dev and the edge
 * only forwards `/api/` in production — so a picker that fetches as you type goes through a
 * `+server.ts` like every other one (`/marketing/accounts` is the precedent).
 *
 * It forwards the query and nothing else. The search is *live* on purpose: a picker showing a
 * stale list is how somebody links a container that was deleted last month, and Tag Manager's
 * per-user-per-minute quota is exactly why the API answers a search rather than a sweep.
 */
import { json } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  const q = event.url.searchParams.get("q") ?? "";
  const { data, error } = await apiFor(event).GET("/api/v1/gtm/containers/available", {
    params: { query: { q } },
  });
  if (error || !data) {
    // The refusal that matters here is `gtm_not_configured` — this account's Google grant does
    // not carry the Tag Manager scopes — and its cure is a reconnect, which the picker draws.
    // Passing the key through rather than one flat "something went wrong" is what lets it.
    return json({ query: q, containers: [], warnings: [], error: apiErrorKey(error).key });
  }
  return json(data);
};
