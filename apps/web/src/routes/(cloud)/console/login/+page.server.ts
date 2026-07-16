import { fail, redirect } from "@sveltejs/kit";

import { apiLogin, AUTH_COOKIE_NAME } from "$lib/core/auth.server";

import type { Actions } from "./$types";

// Console sign-in (epic #199): the apex host has no org, so this is the plain global login
// — the layout then only admits instance owners.
export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    if (!email || !password) return fail(400, { error: "errors.required", email });

    const token = await apiLogin(event, email, password);
    if (!token) return fail(400, { error: "auth.invalid_credentials", email });

    event.cookies.set(AUTH_COOKIE_NAME, token, {
      path: "/",
      httpOnly: true,
      sameSite: "lax",
      secure: event.url.protocol === "https:",
      maxAge: 60 * 60 * 24 * 7,
    });
    throw redirect(303, "/console");
  },
};
