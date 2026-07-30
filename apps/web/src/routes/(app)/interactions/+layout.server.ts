import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The three URL-independent lookups under `interactions/` (#290): the tenant's interaction-kind
 * vocabulary, the member names the owner filter and the rows render, and the company custom
 * fields the inline client quick-create needs.
 *
 * The list page reruns on every search keystroke, date-range click, owner switch and page step;
 * a layout load does not. These were three round-trips per one of those interactions, all
 * answering the same thing (docs/PERFORMANCE.md).
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [kinds, members, companyDefinitions] = await Promise.all([
    api.GET("/api/v1/interactions/kinds", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
  ]);
  return {
    kinds: kinds.data ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
  };
};
