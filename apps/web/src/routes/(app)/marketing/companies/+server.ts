/**
 * Client-callable proxy for the connect dialog's client picker (#338).
 *
 * The same shape as `../accounts/+server.ts` beside it, and for the same two reasons. It is
 * **lazy**: `/marketing` must not pay a client-list read on every render to fill a picker most
 * visits never open (docs/PERFORMANCE.md). And it goes through the request-scoped typed client
 * rather than being fetched from the browser as `/api/v1/companies` directly — that only
 * resolves where the edge forwards `/api/`, which is production and not `vite dev`, so a picker
 * built on it would be empty on every developer's machine and nowhere else (CLAUDE.md §12: a
 * route the edge does not forward is a route nobody has).
 */
import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/** What the picker lists. The API's own ceiling on `limit`, so asking for one more is a 422. */
const LIMIT = 200;

export const GET: RequestHandler = async (event) => {
  const { data } = await apiFor(event).GET("/api/v1/companies", {
    // `count: true` against the house default, and the exception earns itself: the total is the
    // only thing that can tell "these are all of them" from "these are the first two hundred",
    // and this read happens once per dialog opened rather than on every page render.
    params: { query: { limit: LIMIT, offset: 0, count: true, sort: "name" } },
  });
  const rows = data?.items ?? [];
  return json({
    items: rows.map((c) => ({ id: c.id, name: c.name })),
    // A picker showing a prefix of the clients must say so, or the one that is missing reads as
    // one that cannot be connected (CLAUDE.md §9 — no silent caps).
    capped: (data?.total ?? rows.length) > rows.length,
  });
};
