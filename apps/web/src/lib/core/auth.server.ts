/**
 * Server-only auth bridge (CLAUDE.md §3).
 *
 * The API issues the session as an httpOnly cookie. Because the web and API are different
 * hosts, the SSR login action calls the API, extracts the token from its Set-Cookie, and
 * re-sets it on the web's own domain — so later SSR requests forward it back to the API.
 *
 * With 2FA the password is only the first step: the API answers `200 {two_factor_required}`
 * with a short-lived challenge token instead of a cookie, and `/auth/2fa/verify` is what
 * finally yields the Set-Cookie this bridge extracts.
 */
import type { ApiEvent } from "./session";
import { apiBaseUrl } from "./api/client";

export const AUTH_COOKIE_NAME = "schakl_auth";

export type LoginResult =
  | { kind: "session"; token: string }
  | { kind: "challenge"; challengeToken: string; methods: string[] }
  | { kind: "rate_limited" }
  | { kind: "failed" };

function apiHeaders(event: ApiEvent): Record<string, string> {
  const headers: Record<string, string> = {
    "content-type": "application/x-www-form-urlencoded",
    "x-forwarded-host": event.request.headers.get("host") ?? "",
  };
  // Forward the caller's address (as Traefik/Cloudflare set it on the request that reached us)
  // so the API's brute-force limiter buckets per real client. Without it, every SSR login shares
  // the web container's one IP, so a single fumbling user would throttle everyone.
  const forwardedFor = event.request.headers.get("x-forwarded-for");
  if (forwardedFor) headers["x-forwarded-for"] = forwardedFor;
  const cfConnectingIp = event.request.headers.get("cf-connecting-ip");
  if (cfConnectingIp) headers["cf-connecting-ip"] = cfConnectingIp;
  return headers;
}

function tokenFromSetCookie(res: Response): string | null {
  for (const cookie of res.headers.getSetCookie?.() ?? []) {
    if (cookie.startsWith(`${AUTH_COOKIE_NAME}=`)) {
      return cookie.slice(AUTH_COOKIE_NAME.length + 1).split(";")[0];
    }
  }
  return null;
}

export async function apiLogin(
  event: ApiEvent,
  email: string,
  password: string,
): Promise<LoginResult> {
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: apiHeaders(event),
    body: new URLSearchParams({ username: email, password }),
  });
  if (res.status === 429) return { kind: "rate_limited" };
  if (res.status !== 200 && res.status !== 204) return { kind: "failed" };

  const token = tokenFromSetCookie(res);
  if (token) return { kind: "session", token };

  if (res.status === 200) {
    const body = await res.json().catch(() => null);
    if (body?.two_factor_required && body.challenge_token) {
      return {
        kind: "challenge",
        challengeToken: String(body.challenge_token),
        methods: Array.isArray(body.methods) ? body.methods.map(String) : ["totp", "backup"],
      };
    }
  }
  return { kind: "failed" };
}

/** Redeem a login challenge with a code → the session token, or the API's error key. */
export async function apiVerifyTwoFactor(
  event: ApiEvent,
  challengeToken: string,
  code: string,
  method: string,
): Promise<{ token: string } | { errorKey: string }> {
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/auth/2fa/verify`, {
    method: "POST",
    headers: { ...apiHeaders(event), "content-type": "application/json" },
    body: JSON.stringify({ challenge_token: challengeToken, code, method }),
  });
  const token = tokenFromSetCookie(res);
  if (res.ok && token) return { token };
  const body = await res.json().catch(() => null);
  return { errorKey: body?.error?.message ?? "errors.two_factor_code_invalid" };
}

/** Ask the API to text a login code for this challenge → the masked number, or an error key. */
export async function apiSendTwoFactorSms(
  event: ApiEvent,
  challengeToken: string,
): Promise<{ phoneMasked: string } | { errorKey: string }> {
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/auth/2fa/challenge/sms`, {
    method: "POST",
    headers: { ...apiHeaders(event), "content-type": "application/json" },
    body: JSON.stringify({ challenge_token: challengeToken }),
  });
  const body = await res.json().catch(() => null);
  if (res.ok && body?.phone_masked) return { phoneMasked: String(body.phone_masked) };
  return { errorKey: body?.error?.message ?? "errors.server" };
}
