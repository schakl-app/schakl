import { error as httpError, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** A shipped report design's own HTML and CSS, to branch a custom template from. */
export const GET = async (event: RequestEvent) => {
  const design = event.url.searchParams.get("design") ?? "standard";
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/reporting/templates/designs/{design}/source",
    { params: { path: { design } } },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return json(data);
};
