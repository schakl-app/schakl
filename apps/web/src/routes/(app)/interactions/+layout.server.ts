import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The two URL-independent lookups under `interactions/` (#290): the tenant's interaction-kind
 * vocabulary, and the member names the owner filter and the rows render.
 *
 * The list page reruns on every search keystroke, date-range click, owner switch and page step;
 * a layout load does not. These were a round-trip each per one of those interactions, all
 * answering the same thing (docs/PERFORMANCE.md).
 *
 * The company custom fields used to be a third: the inline client quick-create lived on the page
 * and needed them up front. The dialog now sits inside the form and fetches its own on first
 * open, so this no longer bills every visit for a modal most of them never open.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [kinds, members] = await Promise.all([
    api.GET("/api/v1/interactions/kinds", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    kinds: kinds.data ?? [],
    members: members.data ?? [],
  };
};
