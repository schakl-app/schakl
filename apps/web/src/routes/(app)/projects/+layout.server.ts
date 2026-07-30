import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups every screen under `projects/` needs and none of them varies by URL (#290): the client
 * picker, the tenant's project custom fields, and the member names the assignee column and
 * pickers render.
 *
 * A layout load does not rerun on filter, sort or detail navigation, so these three calls happen
 * once per visit to the section instead of once per click — and the detail page, which asked for
 * all three again on top of its own eight, stops paying for them entirely (docs/PERFORMANCE.md).
 *
 * Freshness is not a worry: every quick-create form here is `use:enhance`d, and a successful
 * submit invalidates, which reruns layout loads too — so a client created inline still appears
 * in the picker that opened it.
 *
 * It deliberately does **not** `await event.parent()`. This fan has no dependency on the app
 * layout, and awaiting first would serialise it behind that load for no reason.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [companies, definitions, members] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    companies: companies.data?.items ?? [],
    definitions: definitions.data ?? [],
    members: members.data ?? [],
  };
};
