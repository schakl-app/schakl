import { error as httpError, redirect } from "@sveltejs/kit";

import type { RequestEvent } from "./$types";

import { apiFor } from "$lib/core/session";

/** Streams the org's export as a JSON download (issue #26 data portability). */
export const GET = async (event: RequestEvent) => {
  if (!event.locals.user?.isInstanceAdmin) throw redirect(303, "/");
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
