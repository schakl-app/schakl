import { fail, redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Forgot-password (#161). The API always answers 202 whether or not the address exists —
 * and so does this page: "als het adres bestaat is er gemaild", never a user enumeration.
 * SSO-enforced orgs get the API's 403 and the page says local login is off.
 */
export const load: PageServerLoad = async (event) => {
  if (event.locals.user) throw redirect(303, "/");
  return {};
};

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "").trim();
    if (!email) return fail(400, { error: "errors.required" });
    const { response } = await apiFor(event).POST("/api/v1/auth/forgot-password", {
      body: { email },
    });
    if (response.status === 403) return fail(403, { error: "auth.local_login_disabled" });
    if (response.status === 429) return fail(429, { error: "errors.rate_limited" });
    return { sent: true };
  },
};
