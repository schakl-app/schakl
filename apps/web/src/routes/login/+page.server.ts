import { fail, redirect } from "@sveltejs/kit";

import {
  apiLogin,
  apiSendTwoFactorSms,
  apiVerifyTwoFactor,
  AUTH_COOKIE_NAME,
} from "$lib/core/auth.server";
import { createApiClient } from "$lib/core/api/client";
import { asLocale, LOCALE_COOKIE, LOCALE_COOKIE_OPTIONS } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

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

/** Set the web-domain session cookie, seed the locale preference, land on the dashboard. */
async function establishSession(event: RequestEvent, token: string): Promise<never> {
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
}

export const actions: Actions = {
  login: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");

    if (!email || !password) {
      return fail(400, { error: "errors.required", email });
    }

    const result = await apiLogin(event, email, password);
    if (result.kind === "failed") {
      return fail(400, { error: "auth.invalid_credentials", email });
    }
    if (result.kind === "challenge") {
      // Password accepted; the session now hinges on the second factor. The challenge token is
      // all the browser holds — short-lived, and redeemable only with a valid code.
      return {
        twoFactor: true as const,
        challengeToken: result.challengeToken,
        methods: result.methods,
      };
    }
    return await establishSession(event, result.token);
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const challengeToken = String(form.get("challenge_token") ?? "");
    const code = String(form.get("code") ?? "").trim();
    const method = String(form.get("method") ?? "totp");
    const methods = String(form.get("methods") ?? "totp,backup").split(",");
    const step = { twoFactor: true as const, challengeToken, methods };

    if (!code) return fail(400, { ...step, error: "errors.required" });
    const result = await apiVerifyTwoFactor(event, challengeToken, code, method);
    if ("errorKey" in result) {
      // An expired challenge sends them back to the password step, not into a dead loop.
      if (result.errorKey === "errors.two_factor_challenge_invalid") {
        return fail(401, { error: result.errorKey });
      }
      return fail(400, { ...step, error: result.errorKey });
    }
    return await establishSession(event, result.token);
  },

  sms: async (event) => {
    const form = await event.request.formData();
    const challengeToken = String(form.get("challenge_token") ?? "");
    const methods = String(form.get("methods") ?? "totp,backup").split(",");
    const step = { twoFactor: true as const, challengeToken, methods };

    const result = await apiSendTwoFactorSms(event, challengeToken);
    if ("errorKey" in result) {
      if (result.errorKey === "errors.two_factor_challenge_invalid") {
        return fail(401, { error: result.errorKey });
      }
      return fail(400, { ...step, error: result.errorKey });
    }
    return { ...step, smsSentTo: result.phoneMasked };
  },
};
