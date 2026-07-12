import { apiFor } from "$lib/core/session";
import { interactionActions } from "$lib/modules/interactions/actions.server";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The pending-email review queue (#156): *your* matched Gmail messages awaiting approval,
 * across all clients — the host-page panels show them one client at a time, which is no way
 * to work a queue. Approve/reject/remap are the owner-only review actions the API enforces;
 * `mine` scopes the list to the caller's own mailbox.
 */
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [pending, members] = await Promise.all([
    api.GET("/api/v1/interactions", {
      params: { query: { status: "pending", mine: true, limit: 100 } },
    }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    items: pending.data?.items ?? [],
    total: pending.data?.total ?? 0,
    members: members.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = { ...interactionActions };
