/**
 * Personal dark-mode preference (issue #14). Mirrors `i18n.ts`'s locale-cookie pattern exactly:
 * `users` prefs (`appearance.theme`, via `/api/v1/prefs`) is the cross-device source of truth,
 * this cookie is the per-browser cache `hooks.server.ts` reads on every request to stamp
 * `<html data-theme>` before first paint, with no extra API round-trip per page (see
 * docs/PERFORMANCE.md — an extra `/api/v1/prefs` fetch on every request for a cosmetic setting
 * is not worth it; the Settings → Account page reconciles the cookie against the persisted
 * value when the user actually looks at it).
 */
import { t } from "$lib/core/i18n";

export const THEME_MODES = ["system", "light", "dark"] as const;
export type ThemeMode = (typeof THEME_MODES)[number];

export const THEME_COOKIE = "theme";

/** Same shape as `LOCALE_COOKIE_OPTIONS` — must stay readable by client JS (the inline
 *  no-flash resolver script and the `system`-mode matchMedia listener both read it). */
export const THEME_COOKIE_OPTIONS = {
  path: "/",
  maxAge: 60 * 60 * 24 * 365,
  sameSite: "lax",
  httpOnly: false,
} as const;

/** Narrow an arbitrary string to a supported mode; `null` for anything else. */
export function asThemeMode(value: string | null | undefined): ThemeMode | null {
  return value && (THEME_MODES as readonly string[]).includes(value) ? (value as ThemeMode) : null;
}

export function themeModeLabel(mode: ThemeMode): string {
  return t(`theme_mode.${mode}`);
}

/** Read a valid mode out of a raw `Cookie:` header (server) or `document.cookie` (client). */
export function parseThemeCookie(cookieHeader: string | null | undefined): ThemeMode | null {
  if (!cookieHeader) return null;
  const prefix = THEME_COOKIE + "=";
  const value = cookieHeader
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length);
  return asThemeMode(value);
}
