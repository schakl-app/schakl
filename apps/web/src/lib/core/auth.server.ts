/**
 * Server-only auth bridge (CLAUDE.md §3).
 *
 * The API issues the session as an httpOnly cookie. Because the web and API are different
 * hosts, the SSR login action calls the API, extracts the token from its Set-Cookie, and
 * re-sets it on the web's own domain — so later SSR requests forward it back to the API.
 */
import type { ApiEvent } from "./session";
import { apiBaseUrl } from "./api/client";

export const AUTH_COOKIE_NAME = "schakl_auth";

export async function apiLogin(
  event: ApiEvent,
  email: string,
  password: string,
): Promise<string | null> {
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
    body: new URLSearchParams({ username: email, password }),
  });
  if (res.status !== 200 && res.status !== 204) return null;

  for (const cookie of res.headers.getSetCookie?.() ?? []) {
    if (cookie.startsWith(`${AUTH_COOKIE_NAME}=`)) {
      return cookie.slice(AUTH_COOKIE_NAME.length + 1).split(";")[0];
    }
  }
  return null;
}
