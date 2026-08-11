import { fail, redirect } from "@sveltejs/kit";

import { apiLogin, AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { safeInternalPath } from "$lib/core/redirect";

import type { Actions, PageServerLoad } from "./$types";

// Console sign-in (epic #199): the apex host has no org, so this is the plain global login
// — the layout then only admits instance owners.
//
// `next` works exactly as it does on the tenant login screen: the layout's guard puts it on the
// URL, the form carries it back (a form action posts to `?/`, so the page's own query string is
// not on the request), and a failed attempt echoes it so the retry still knows.
export const load: PageServerLoad = async (event) => ({
  next: safeInternalPath(event.url.searchParams.get("next")),
});

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    const next = safeInternalPath(form.get("next"));
    if (!email || !password) return fail(400, { error: "errors.required", email, next });

    const result = await apiLogin(event, email, password);
    if (result.kind === "challenge") {
      // The owner enrolled a second factor: the console has no verify step, but the session
      // the normal login page issues after 2FA is just as valid here — point them there.
      return fail(400, { error: "cloud.console.two_factor_via_login", email, next });
    }
    if (result.kind !== "session") {
      return fail(400, { error: "auth.invalid_credentials", email, next });
    }

    event.cookies.set(AUTH_COOKIE_NAME, result.token, {
      path: "/",
      httpOnly: true,
      sameSite: "lax",
      secure: event.url.protocol === "https:",
      maxAge: 60 * 60 * 24 * 7,
    });
    throw redirect(303, next ?? "/console");
  },
};
