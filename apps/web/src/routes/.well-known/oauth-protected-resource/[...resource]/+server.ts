import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/**
 * RFC 9728 protected-resource metadata for one `/mcp` URL. Served here for the same reason as
 * its sibling: the RFC puts it on the root of the host, and the root belongs to the web app
 * (docs/MCP.md).
 *
 * A **catch-all** rather than one route per section, because RFC 9728 §3.1 forms the metadata
 * URL by inserting the well-known segment between host and path — so `https://host/mcp/google-ads`
 * is described at `/.well-known/oauth-protected-resource/mcp/google-ads`. There are thirty
 * sections and there will be more the next time a module ships; a route per section would be a
 * list that goes stale in exactly the way `app/core/mcp/sections.py` exists to avoid.
 *
 * The path is handed to the API as a *parameter* and validated there against a pattern, because
 * it is echoed into the document as the resource identifier a token gets audience-bound to. An
 * unrecognised one is refused rather than reflected.
 */
export const GET: RequestHandler = async (event) => {
  const resource = `/${event.params.resource ?? "mcp"}`;
  const { data, error } = await apiFor(event).GET("/api/v1/oauth/metadata/protected-resource", {
    params: { query: { resource_path: resource } },
  });
  // 404, not 502: a path this server does not protect has no metadata, and saying "server
  // error" would send a client into a retry over something that will never succeed.
  if (error || !data) return json({ error: "not_found" }, { status: 404 });
  return json(data, {
    headers: {
      "Cache-Control": "public, max-age=300",
      "Access-Control-Allow-Origin": "*",
    },
  });
};
