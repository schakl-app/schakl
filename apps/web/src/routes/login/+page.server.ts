import { fail, redirect } from "@sveltejs/kit";

import {
  apiLogin,
  apiSendTwoFactorSms,
  apiVerifyTwoFactor,
  establishSession,
} from "$lib/core/auth.server";
import { apiFor } from "$lib/core/session";
import { safeInternalPath } from "$lib/core/redirect";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

/** Why an SSO round-trip bounced back here. The API redirects to `/login?error=…` — the only
 * way it can report anything at all, since the browser is mid-redirect and there is no
 * response body to carry an error envelope. An unrecognised value says nothing rather than
 * echoing whatever was in the URL. */
const SSO_ERRORS: Record<string, string> = {
  oidc: "auth.sso_failed",
  oidc_no_access: "auth.sso_no_access",
};

export const load: PageServerLoad = async (event) => {
  // `next` is where a *guarded* screen sent them (`(app)/+layout.server.ts` on a cold deep link,
  // the session-ended dialog's fallback on a warm one), so signing in has to land there rather
  // than on the dashboard — arriving somewhere else is how "log back in" turns into "find your
  // way back again".
  const next = safeInternalPath(event.url.searchParams.get("next"));
  if (event.locals.user) throw redirect(303, next ?? "/");
  // Per-org at request time (#76): the API resolves the org from the hostname and answers
  // from its *stored* SSO settings, so the button follows a settings save with no restart.
  const { data } = await apiFor(event).GET("/api/v1/meta/modules");
  return {
    localLoginEnabled: data?.local_login_enabled ?? true,
    oidcEnabled: data?.oidc_enabled ?? false,
    oidcName: data?.oidc_name ?? null,
    error: SSO_ERRORS[event.url.searchParams.get("error") ?? ""] ?? null,
    next,
  };
};

/** Set the session up on this browser, then land where they were headed. */
async function signIn(event: RequestEvent, token: string, next: string | null): Promise<never> {
  await establishSession(event, token);
  throw redirect(303, next ?? "/");
}

// Every action result carries `next` back, and every one of them is a re-render of this page.
//
// With JS the page URL never changes, so `data.next` from the original load would have done. But
// a form action posts to `?/login` — so *without* JS the browser lands on `/login?/login`, the
// query string the target arrived in is gone, `load` re-reads a `next` that is no longer there,
// and the retry after one mistyped password silently forgets the deep link. The same hole swallows
// it on the "send me an SMS" step, which is a re-render by design and not a failure at all.
//
// So the form's own value is the authority on the way back out, exactly as it is on the way in.
export const actions: Actions = {
  login: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    // Carried on the form, not read back off `event.url`: a form action posts to `?/login`, so
    // the page's own query string is not on the request that redeems the credentials.
    const next = safeInternalPath(form.get("next"));

    if (!email || !password) {
      return fail(400, { error: "errors.required", email, next });
    }

    const result = await apiLogin(event, email, password);
    if (result.kind === "rate_limited") {
      // Too many attempts from this client — the API throttled us. Say so plainly rather than
      // "wrong password", which would be misleading (the credentials were never checked).
      return fail(429, { error: "errors.rate_limited", email, next });
    }
    if (result.kind === "failed") {
      return fail(400, { error: "auth.invalid_credentials", email, next });
    }
    if (result.kind === "challenge") {
      // Password accepted; the session now hinges on the second factor. The challenge token is
      // all the browser holds — short-lived, and redeemable only with a valid code.
      return {
        twoFactor: true as const,
        challengeToken: result.challengeToken,
        methods: result.methods,
        next,
      };
    }
    return await signIn(event, result.token, next);
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const next = safeInternalPath(form.get("next"));
    const challengeToken = String(form.get("challenge_token") ?? "");
    const code = String(form.get("code") ?? "").trim();
    const method = String(form.get("method") ?? "totp");
    const methods = String(form.get("methods") ?? "totp,backup").split(",");
    const step = { twoFactor: true as const, challengeToken, methods, next };

    if (!code) return fail(400, { ...step, error: "errors.required" });
    const result = await apiVerifyTwoFactor(event, challengeToken, code, method);
    if ("errorKey" in result) {
      // An expired challenge sends them back to the password step, not into a dead loop — and
      // not back to the dashboard either, so `next` survives the restart.
      if (result.errorKey === "errors.two_factor_challenge_invalid") {
        return fail(401, { error: result.errorKey, next });
      }
      return fail(400, { ...step, error: result.errorKey });
    }
    return await signIn(event, result.token, next);
  },

  sms: async (event) => {
    const form = await event.request.formData();
    const next = safeInternalPath(form.get("next"));
    const challengeToken = String(form.get("challenge_token") ?? "");
    const methods = String(form.get("methods") ?? "totp,backup").split(",");
    const step = { twoFactor: true as const, challengeToken, methods, next };

    const result = await apiSendTwoFactorSms(event, challengeToken);
    if ("errorKey" in result) {
      if (result.errorKey === "errors.two_factor_challenge_invalid") {
        return fail(401, { error: result.errorKey, next });
      }
      return fail(400, { ...step, error: result.errorKey });
    }
    return { ...step, smsSentTo: result.phoneMasked };
  },
};
