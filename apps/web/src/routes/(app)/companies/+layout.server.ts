import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups every screen under `companies/` needs and none of them varies by URL (#290): the
 * tenant's company custom fields and the member names the assignee column and pickers render.
 *
 * A layout load does not rerun on filter, sort or detail navigation, so these cost one call per
 * visit to the section instead of one per click — and the detail page, which fetched both again
 * on top of its own five, stops paying for them (docs/PERFORMANCE.md).
 *
 * Freshness rides `invalidateAll`: every quick-create form here is `use:enhance`d, and a
 * successful submit reruns layout loads too.
 *
 * It deliberately does **not** `await event.parent()` — this fan depends on nothing the app
 * layout produces, and awaiting first would serialise it behind that load for no reason.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [definitions, members] = await Promise.all([
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    definitions: definitions.data ?? [],
    members: members.data ?? [],
  };
};
