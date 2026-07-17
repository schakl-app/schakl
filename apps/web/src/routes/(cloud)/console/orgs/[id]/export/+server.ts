import { error as httpError } from "@sveltejs/kit";

import type { RequestEvent } from "./$types";

import { apiFor } from "$lib/core/session";

/** Streams the org's export as a JSON download from the console (epic #199). The API gate
 * (superuser + claimed service PIN) is the boundary — a locked org answers 403 → 404 here. */
export const GET = async (event: RequestEvent) => {
  const api = apiFor(event);
  const [{ data }, { data: org }] = await Promise.all([
    api.GET("/api/v1/instance/orgs/{org_id}/export", {
      params: { path: { org_id: event.params.id } },
    }),
    api.GET("/api/v1/instance/orgs/{org_id}", {
      params: { path: { org_id: event.params.id } },
    }),
  ]);
  if (!data) throw httpError(404);
  return new Response(JSON.stringify(data, null, 2), {
    headers: {
      "content-type": "application/json",
      "content-disposition": `attachment; filename="schakl-export-${org?.slug ?? event.params.id}.json"`,
    },
  });
};
