import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { channelActions } from "$lib/modules/notifications/channels.server";
import { parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Org-wide defaults: what a member inherits before they touch their own settings. Manager-gated,
// because it is org config — and org config lives under Instellingen (docs/UX.md §6).
//
// Since #295 the org's **shared rooms** live here too, and for the same reason: `#crm` is one
// routing for the whole agency, not something each member inherits and then overrides. Each room
// is a column of the matrix above, so "which events, how often" is set exactly where an
// employee's own Slack is set.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "notifications.defaults.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const canManageChannels = can(event.locals.user, "notifications.channels.manage");
  const [prefs, channels] = await Promise.all([
    api.GET("/api/v1/notifications/preferences/defaults"),
    canManageChannels
      ? api.GET("/api/v1/notifications/channels")
      : Promise.resolve({ data: null, error: undefined }),
  ]);
  // Same rule as the personal page: a matrix that did not load is a page that refuses, because
  // the form posts wholesale and a Save over nothing clears the org's every default and room.
  if (prefs.error || !prefs.data || channels.error) {
    throw error(502, "settings.notifications.unavailable");
  }
  return {
    matrix: prefs.data,
    canManageChannels,
    /** The org's shared rooms. The API hands an admin every channel; only these are routed here. */
    channels: (channels.data ?? []).filter((c) => c.user_id == null),
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const raw = form.get("payload");
    // Empty = the server-rendered field of a form that had not hydrated (`prefs.server.ts`).
    if (raw === "") return fail(400, { error: "errors.form_not_ready" });
    const body = parseMatrixPayload(raw);
    if (!body) return fail(400, { error: "errors.validation" });

    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences/defaults", {
      body,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  /** Delete the org's rows: every event falls back to the platform's own defaults, and every
   *  shared room goes quiet until it is routed again. */
  reset: async (event) => {
    const { error } = await apiFor(event).PUT("/api/v1/notifications/preferences/defaults", {
      body: { events: [], general: null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  ...channelActions("org"),
};
