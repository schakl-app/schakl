import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The URL-independent lookups under `interactions/` (#290): the tenant's interaction-kind
 * vocabulary, the member names the owner filter and the rows render, and — since #341 — the
 * caller's own Gmail sync state, which the timeline's "laatst gescand" line and its scan
 * button are drawn from.
 *
 * The list page reruns on every search keystroke, date-range click, owner switch and page step;
 * a layout load does not. These were a round-trip each per one of those interactions, all
 * answering the same thing (docs/PERFORMANCE.md).
 *
 * The Gmail read is asked **only of someone who could act on the answer**: it is two indexed
 * single-row reads, but a caller without `google.connection.manage` (or on an instance where
 * the module is not enabled at all, where the permission cannot be held) would pay for a 403
 * on every visit to learn nothing. Mirroring the API's own key is the rule either way (§15).
 *
 * The company custom fields used to be a third: the inline client quick-create lived on the page
 * and needed them up front. The dialog now sits inside the form and fetches its own on first
 * open, so this no longer bills every visit for a modal most of them never open.
 *
 * The **size of the review queue** is the fourth, and it belongs here for the same reason the
 * other three do: it is a property of the viewer's mailbox, not of the filters on screen, so it
 * must read the same whatever the page is narrowed to — a "Te beoordelen 11" that fell to 2
 * because somebody searched would be counting the search. `limit: 1` because only the total is
 * drawn; the page below fetches its own rows.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const wantsGmail = can(event.locals.user, "google.connection.manage");
  const wantsQueue = can(event.locals.user, "interactions.interaction.read");
  const [kinds, members, gmail, queue] = await Promise.all([
    api.GET("/api/v1/interactions/kinds", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/members/lookup"),
    wantsGmail ? api.GET("/api/v1/google/gmail/status") : Promise.resolve(null),
    wantsQueue
      ? api.GET("/api/v1/interactions", {
          params: { query: { status: "pending", mine: true, limit: 1 } },
        })
      : Promise.resolve(null),
  ]);
  return {
    kinds: kinds.data ?? [],
    members: members.data ?? [],
    /** `null` on an instance without the Google module, or for a caller who cannot connect one. */
    gmailStatus: gmail?.data ?? null,
    /**
     * How many of the viewer's own contact moments are still unreviewed — the number on the
     * Te beoordelen tab, and the only thing on the screen that says an empty queue is empty
     * rather than filtered. A pending row is private to its owner (#172), so this is always
     * "mine": there is no team total to disagree with it.
     */
    pendingTotal: queue?.data?.total ?? 0,
  };
};
