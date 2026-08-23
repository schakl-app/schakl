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

type MessageFn = (params?: Record<string, unknown>, options?: { locale: string }) => string;

export function t(key: string, params?: Record<string, unknown>): string {
  const fn = (messages as unknown as Record<string, MessageFn>)[key];
  return fn ? fn(params) : key;
}

/**
 * A counted message: `<key>_one` when there is exactly one of the thing, `<key>` otherwise.
 *
 * Paraglide here does not compile ICU `{n, plural, …}` — it ships the whole construct as garbage
 * — so a plural is a **pair of keys** and the reader's side picks (CLAUDE.md §8). That convention
 * existed and was applied one string at a time, which is how "1 contactmomenten", "1 taken" and a
 * digest mail headed "1 nieuwe meldingen" all shipped (#343): the pair is invisible in a diff, and
 * the ternary was re-typed in five files that each spelled it slightly differently.
 *
 * `count` is passed through as a parameter, so a call site names the number once.
 *
 * A `_one` that has not been written yet falls back to the plural rather than rendering the raw
 * key at the user — a missing singular is a copy defect, not a broken screen. It is a *build*
 * failure instead: `scripts/i18n-check.mjs` fails a `_one` with no sibling and a counted key with
 * no `_one`, so the fallback can never be what ships.
 */
export function tn(key: string, count: number, params?: Record<string, unknown>): string {
  const merged = { count, ...params };
  if (count !== 1) return t(key, merged);
  const singular = t(`${key}_one`, merged);
  return singular === `${key}_one` ? t(key, merged) : singular;
}

/**
 * `t()` for a caller that must **name** the locale instead of inheriting the request's.
 *
 * Paraglide resolves the locale from an AsyncLocalStorage store bound by its middleware
 * (hooks.server.ts). Exactly one surface renders outside that middleware on purpose: the
 * standalone error document (`errors/standalone.server.ts`), which exists for the request where
 * the tenant fetch failed and the hook chain never got that far. It has a locale — the cookie,
 * or the last-known tenant default — and no way to bind one, so it passes it.
 *
 * Not a general escape hatch: anywhere the middleware *has* run, `t()` is the call.
 */
export function tIn(
  locale: string | null | undefined,
  key: string,
  params?: Record<string, unknown>,
): string {
  const fn = (messages as unknown as Record<string, MessageFn>)[key];
  if (!fn) return key;
  return locale ? fn(params, { locale }) : fn(params);
}

/**
 * Whether the catalogue actually holds this key.
 *
 * `t()` degrades a missing key to the key itself, which is right for a dynamic key the API hands
 * us and useless for *choosing between* two keys — `t(k) === k` cannot tell a missing message
 * from one whose text happens to equal its own name. Callers that offer an optional variant (the
 * system-voice phrasing of an actor-prefixed event, #358) ask here first.
 */
export function hasMessage(key: string): boolean {
  return typeof (messages as unknown as Record<string, MessageFn>)[key] === "function";
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
