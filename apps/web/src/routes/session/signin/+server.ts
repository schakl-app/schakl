import { error, json } from "@sveltejs/kit";

import {
  apiLogin,
  apiSendTwoFactorSms,
  apiVerifyTwoFactor,
  establishSession,
} from "$lib/core/auth.server";

import type { RequestHandler } from "./$types";

/** An unparseable `Origin` matches no host, so it is refused like any other mismatch. */
function originHost(origin: string): string | null {
  try {
    return new URL(origin).host;
  } catch {
    return null;
  }
}

/**
 * Sign back in **without leaving the page** (the session-ended dialog).
 *
 * Every credential path here is the login screen's own — `apiLogin`, `apiVerifyTwoFactor`,
 * `apiSendTwoFactorSms`, `establishSession` — so the brute-force bucket (which keys on the
 * forwarded client IP), the 2FA challenge and the cookie flags are one implementation, not two
 * that look alike. What differs is only the answer: JSON the dialog can act on, instead of a
 * redirect that would throw the page away. Throwing the page away is the entire bug this
 * exists to fix — a half-written note, a filtered list, a scrolled position.
 *
 * JSON rather than a form action on purpose: a cross-origin page cannot POST
 * `application/json` without a CORS preflight we never answer, which is the same protection
 * SvelteKit's CSRF check gives form posts. The Origin comparison below is the belt to that
 * brace, and compares against the `Host` the rest of the app already trusts to name the tenant.
 */
export const POST: RequestHandler = async (event) => {
  const origin = event.request.headers.get("origin");
  const host = event.request.headers.get("host");
  if (origin && host && originHost(origin) !== host) throw error(403, "forbidden");

  const body = await event.request.json().catch(() => null);
  if (!body || typeof body !== "object") return json({ error: "errors.server" }, { status: 400 });

  const challengeToken = typeof body.challengeToken === "string" ? body.challengeToken : "";

  // Step 2b — text a code for a challenge already in hand.
  if (challengeToken && body.sms === true) {
    const sent = await apiSendTwoFactorSms(event, challengeToken);
    if ("errorKey" in sent) return json({ error: sent.errorKey }, { status: 400 });
    return json({ smsSentTo: sent.phoneMasked });
  }

  // Step 2a — redeem a challenge with a code.
  if (challengeToken) {
    const code = String(body.code ?? "").trim();
    const method = String(body.method ?? "totp");
    if (!code) return json({ error: "errors.required" }, { status: 400 });
    const verified = await apiVerifyTwoFactor(event, challengeToken, code, method);
    if ("errorKey" in verified) {
      // An expired challenge drops the dialog back to the password step rather than looping on
      // a token that can never be redeemed again.
      const expired = verified.errorKey === "errors.two_factor_challenge_invalid";
      return json({ error: verified.errorKey, restart: expired }, { status: 400 });
    }
    const { userId } = await establishSession(event, verified.token);
    return json({ ok: true, userId });
  }

  // Step 1 — the password.
  const email = String(body.email ?? "").trim();
  const password = String(body.password ?? "");
  if (!email || !password) return json({ error: "errors.required" }, { status: 400 });

  const result = await apiLogin(event, email, password);
  if (result.kind === "rate_limited") {
    // The credentials were never checked, so "wrong password" would be a lie.
    return json({ error: "errors.rate_limited" }, { status: 429 });
  }
  if (result.kind === "failed") {
    return json({ error: "auth.invalid_credentials" }, { status: 400 });
  }
  if (result.kind === "challenge") {
    return json({
      twoFactor: true,
      challengeToken: result.challengeToken,
      methods: result.methods,
    });
  }
  const { userId } = await establishSession(event, result.token);
  return json({ ok: true, userId });
};
