/**
 * Client half of the `custom-vlotrDefault` Paraglide strategy (CLAUDE.md §8).
 *
 * See `paraglide-strategy.server.ts` for the full strategy rationale. This half is imported
 * for its side effect from `hooks.client.ts` and must stay free of any server-only imports
 * (the API client reads private env) — otherwise the whole chain is pulled into the browser
 * bundle and SvelteKit's server-only guard fails the client build.
 *
 * On the client the built-in `cookie` strategy already runs first, so this only fires when no
 * readable cookie is set: fall back to the locale the server resolved, which it stamps on
 * `<html data-locale>`.
 *
 * NEVER fall back to `<html lang>`: that is the European formatting tag (`en-GB`), not a
 * locale code. Returning it made Paraglide's `assertIsLocale` throw during hydration, which
 * tore the page down — the `nl → en` white screen. Everything returned from here is validated,
 * so an unknown value yields `undefined` and Paraglide falls through to `baseLocale`.
 */
import { asLocale, parseLocaleCookie } from "./i18n";
import { defineCustomClientStrategy } from "$lib/paraglide/runtime";

defineCustomClientStrategy("custom-vlotrDefault", {
  getLocale: (): string | undefined =>
    parseLocaleCookie(document.cookie) ??
    asLocale(document.documentElement.dataset.locale) ??
    undefined,
  // Language changes go through the `/set-locale` server round-trip (persists the per-user
  // preference + refreshes the cookie), so client-side setLocale is intentionally a no-op.
  setLocale: () => {},
});
