import { error, fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { channelActions } from "$lib/modules/notifications/channels.server";
import { parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Personal delivery preferences — reachable by every member (NOT manager-gated, unlike the org
// defaults next door). Reached from the profile menu, because what reaches *me* is mine
// (docs/UX.md §6). E-mail is per event inside the matrix (#245), and so is every channel I
// connected myself (#283).
//
// **This page shows my channels and nothing else** (#295). The org's shared rooms used to sit in
// a second list underneath for whoever happened to be an admin, which asked the reader to work
// out why there were two — and left the room's routing outside the matrix entirely. They now live
// on Instellingen → Standaard meldingen, next to the org matrix that routes them.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const canManageOwnChannels = can(event.locals.user, "notifications.channels.manage_own");
  const [prefs, channels] = await Promise.all([
    api.GET("/api/v1/notifications/preferences"),
    canManageOwnChannels
      ? api.GET("/api/v1/notifications/channels")
      : Promise.resolve({ data: null, error: undefined }),
  ]);
  // A read that failed is a page that refuses, never an empty matrix. The form posts the scope's
  // overrides wholesale, and it derives them from what loaded: rendered over nothing, one Save
  // wrote "no overrides" and deleted every row the person had — with "Voorkeuren opgeslagen"
  // above it. The same holds for the channel list, one column over: no columns posts no routes.
  if (prefs.error || !prefs.data || channels.error) {
    throw error(502, "settings.notifications.unavailable");
  }
  // An admin's list also carries the shared rooms (the API scopes by capability, not by page),
  // so filter to mine: they are configured next door, and the matrix here has no column for them.
  const me = event.locals.user?.id ?? "";
  return {
    matrix: prefs.data,
    canManageOwnChannels,
    channels: (channels.data ?? []).filter((c) => c.user_id === me),
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const raw = form.get("payload");
    // The payload is computed in the browser; a submit that arrived before hydration carries
    // the empty server-rendered field (`prefs.server.ts`). Refuse it, or the native POST drops
    // every unsaved change and the page reports success.
    if (raw === "") return fail(400, { error: "errors.form_not_ready" });
    const body = parseMatrixPayload(raw);
    if (!body) return fail(400, { error: "errors.validation" });

    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  /** Delete this user's rows: every event falls back to what the org (or the code) says. */
  reset: async (event) => {
    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences", {
      body: { events: [], general: null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  ...channelActions("user"),
};
