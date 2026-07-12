import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Reset / set a password (#161) — the link in the reset *and* invite emails lands here
 * (`?token=…`). A rejected password surfaces the API's i18n reason (the manager's policy
 * travels through the error envelope); anything else on a 400 is a bad or expired token.
 */
export const load: PageServerLoad = async (event) => {
  if (event.locals.user) throw redirect(303, "/");
  return { token: event.url.searchParams.get("token") ?? "" };
};

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const token = String(form.get("token") ?? "");
    const password = String(form.get("password") ?? "");
    const confirm = String(form.get("confirm") ?? "");
    if (!token || !password) return fail(400, { error: "errors.required" });
    if (password !== confirm) return fail(400, { error: "errors.password_mismatch" });

    const { error } = await apiFor(event).POST("/api/v1/auth/reset-password", {
      body: { token, password },
    });
    if (!error) return { done: true };
    const key = apiErrorKey(error).key;
    // A password-policy rejection names itself; the generic 400 is the token being bad.
    return fail(400, {
      error: key === "errors.validation" ? "errors.reset_token_invalid" : key,
    });
  },
};
