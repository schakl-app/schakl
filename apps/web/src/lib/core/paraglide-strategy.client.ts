/**
 * Client half of the `custom-vlotrDefault` Paraglide strategy (CLAUDE.md §8).
 *
 * See `paraglide-strategy.server.ts` for the full strategy rationale. This half is imported
 * for its side effect from `hooks.client.ts` and must stay free of any server-only imports
 * (the API client reads private env) — otherwise the whole chain is pulled into the browser
 * bundle and SvelteKit's server-only guard fails the client build.
 *
 * On the client the built-in `cookie` strategy already runs first, so this only fires when no
 * cookie is set: fall back to whatever locale the server resolved (stamped on `<html lang>`).
 */
import { parseLocaleCookie } from "./i18n";
import { defineCustomClientStrategy } from "$lib/paraglide/runtime";

defineCustomClientStrategy("custom-vlotrDefault", {
  getLocale: (): string | undefined =>
    parseLocaleCookie(document.cookie) ?? (document.documentElement.lang || undefined),
  // Language changes go through the `/set-locale` server round-trip (sets cookie + persists
  // the per-user preference), so client-side setLocale is intentionally a no-op.
  setLocale: () => {},
});
