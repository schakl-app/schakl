import { fail, redirect } from "@sveltejs/kit";

import { apiLogin, AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { createApiClient } from "$lib/core/api/client";
import { asLocale, LOCALE_COOKIE, LOCALE_COOKIE_OPTIONS } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (event.locals.user) throw redirect(303, "/");
  // Per-org at request time (#76): the API resolves the org from the hostname and answers
  // from its *stored* SSO settings, so the button follows a settings save with no restart.
  const { data } = await apiFor(event).GET("/api/v1/meta/modules");
  return {
    localLoginEnabled: data?.local_login_enabled ?? true,
    oidcEnabled: data?.oidc_enabled ?? false,
    oidcName: data?.oidc_name ?? null,
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

    // Seed the display locale from the user's saved preference so their language follows them to
    // this device. The just-set auth cookie isn't on the incoming request yet, so authenticate
    // the lookup with the fresh token explicitly.
    const authed = createApiClient({
      fetch: event.fetch,
      cookie: `${AUTH_COOKIE_NAME}=${token}`,
      host: event.request.headers.get("host"),
    });
    const { data: me } = await authed.GET("/api/v1/meta/me");
    const locale = asLocale(me?.locale);
    if (locale) event.cookies.set(LOCALE_COOKIE, locale, LOCALE_COOKIE_OPTIONS);
    throw redirect(303, "/");
  },
};
