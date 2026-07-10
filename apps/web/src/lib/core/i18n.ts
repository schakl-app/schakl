/**
 * Web i18n helper (CLAUDE.md §8).
 *
 * Our message keys are flat + **dotted** (`companies.title`), so Paraglide exports them under
 * their exact dotted names (`m["companies.title"]`), which aren't valid `m.x` accessors. `t()`
 * is the single bridge used across the app: it looks the dotted key up directly. It also
 * translates **dynamic** keys the API hands us (error-envelope messages, panel `title_key`s).
 */
import * as messages from "$lib/paraglide/messages";
import { cookieName, locales } from "$lib/paraglide/runtime";

export const LOCALES = locales;
export const LOCALE_COOKIE = cookieName;

/**
 * The locale cookie is a **cache of the `users.locale` preference**, not a credential, and both
 * Paraglide strategies read it from `document.cookie` in the browser — so it must not be
 * `httpOnly`. SvelteKit's `cookies.set` defaults to `httpOnly: true`; that default made the
 * cookie invisible to the client and white-paged the app on a language switch. Always spread
 * these options rather than restating them.
 */
export const LOCALE_COOKIE_OPTIONS = {
  path: "/",
  maxAge: 60 * 60 * 24 * 365,
  sameSite: "lax",
  httpOnly: false,
} as const;

/** Narrow an arbitrary string to a supported locale; `null` for anything else. */
export function asLocale(value: string | null | undefined): string | null {
  return value && (LOCALES as readonly string[]).includes(value) ? value : null;
}

type MessageFn = (params?: Record<string, unknown>) => string;

export function t(key: string, params?: Record<string, unknown>): string {
  const fn = (messages as unknown as Record<string, MessageFn>)[key];
  return fn ? fn(params) : key;
}

export function localeLabel(locale: string): string {
  return t(`locale.${locale}`);
}

/**
 * Read a valid locale out of a raw `Cookie:` header (server) or `document.cookie` (client).
 * Returns `null` when the cookie is absent or holds an unsupported locale — the caller then
 * falls back to the org default. This is the single source of truth for the explicit choice,
 * used by the Paraglide `custom-schaklDefault` strategy so switching actually sticks.
 */
export function parseLocaleCookie(cookieHeader: string | null | undefined): string | null {
  if (!cookieHeader) return null;
  const prefix = LOCALE_COOKIE + "=";
  const value = cookieHeader
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length);
  return asLocale(value);
}
