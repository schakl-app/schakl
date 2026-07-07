import { fail, redirect } from "@sveltejs/kit";

import { apiLogin, AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (event.locals.user) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/meta/modules");
  return {
    localLoginEnabled: data?.local_login_enabled ?? true,
    oidcEnabled: data?.oidc_enabled ?? false,
  };
};

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");

    if (!email || !password) {
      return fail(400, { error: "errors.required", email });
    }

    const token = await apiLogin(event, email, password);
    if (!token) {
      return fail(400, { error: "auth.invalid_credentials", email });
    }

    event.cookies.set(AUTH_COOKIE_NAME, token, {
      path: "/",
      httpOnly: true,
      sameSite: "lax",
      secure: event.url.protocol === "https:",
      maxAge: 60 * 60 * 24 * 7,
    });
    throw redirect(303, "/");
  },
};
