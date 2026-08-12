import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/**
 * RFC 8414 authorization-server metadata, served by the web app because the RFC puts it on the
 * root of the host — and the root of the host is the web app's (docs/MCP.md).
 *
 * The edge routes exactly `/api/` and `/mcp` to the API service and everything else here
 * (CLAUDE.md §12: *a route the edge does not forward is a route nobody has*). Serving this from
 * the API would mean every existing self-hosted install had to update its Traefik config before
 * a connector worked, with the failure mode "Add connector does nothing" and no screen able to
 * explain it. This way nothing at the edge changes.
 *
 * It is a **proxy, not a copy**. The document names the token, registration and revocation
 * endpoints, and a second literal of those URLs living here is a second thing to forget when one
 * moves. The API authors it; this route carries it to where the RFC says to look.
 */
export const GET: RequestHandler = async (event) => {
  const { data, error } = await apiFor(event).GET("/api/v1/oauth/metadata/authorization-server");
  if (error || !data) return json({ error: "server_error" }, { status: 502 });
  return json(data, {
    headers: {
      // Discovery is read once per connection attempt and changes only on deploy. Cached
      // briefly so a client that probes twice does not pay for it twice, and never for so long
      // that a moved endpoint outlives a release.
      "Cache-Control": "public, max-age=300",
      // A connector fetches this cross-origin, from a client the tenant chose, not from us.
      "Access-Control-Allow-Origin": "*",
    },
  });
};
