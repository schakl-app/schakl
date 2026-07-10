import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { EMPTY_MATRIX, parseMatrixPayload } from "$lib/modules/notifications/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

// Personal delivery preferences — reachable by every member (NOT manager-gated, unlike the org
// defaults next door). Reached from the profile menu, because what reaches *me* is mine
// (docs/UX.md §6).
export const load: PageServerLoad = async (event) => {
  const { data } = await apiFor(event).GET("/api/v1/notifications/preferences");
  return { matrix: data ?? EMPTY_MATRIX };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const body = parseMatrixPayload(form.get("payload"));
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
};
