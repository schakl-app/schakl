import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Marketing (#134): the org's Google Ads developer token, stored encrypted per-org
// rather than as instance env config. Admin-only (marketing.link.manage); the token is write-only —
// the API reports `ads_developer_token_configured` and never plays the value back.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "marketing.link.manage")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/marketing/settings");
  return { settings: data ?? null };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    // Empty means "keep the stored token" — the API never returns it.
    const token = String(form.get("ads_developer_token") ?? "").trim() || null;
    const { error } = await apiFor(event).PUT("/api/v1/marketing/settings", {
      body: { ads_developer_token: token },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
