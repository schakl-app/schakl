/**
 * Server half of the `custom-vlotrDefault` Paraglide strategy (CLAUDE.md §8).
 *
 * The compiled strategy order is `cookie → custom-vlotrDefault → baseLocale`. The intent:
 *   1. a user's explicit choice (the `PARAGLIDE_LOCALE` cookie) wins;
 *   2. otherwise fall back to the **tenant's** default locale (`org_settings.default_locale`) —
 *      the app ships showing `nl` even though `en` is the source locale;
 *   3. `baseLocale` (`en`) is the last-resort fallback.
 *
 * IMPORTANT: on the server, Paraglide evaluates *custom* strategies before the built-in
 * `cookie` strategy (`extractLocaleFromRequestAsync`), so this strategy must itself honour the
 * cookie first — otherwise it short-circuits the user's choice and pins the app to one locale.
 *
 * This module reaches the API client (which reads private env via `locale-default`), so it is
 * imported ONLY from `hooks.server.ts` — never from client-reachable code. The tenant lookup
 * is `import()`ed lazily so the API client is loaded only when actually needed.
 */
import { parseLocaleCookie } from "./i18n";
import { defineCustomServerStrategy } from "$lib/paraglide/runtime";

defineCustomServerStrategy("custom-vlotrDefault", {
  getLocale: async (request?: Request): Promise<string | undefined> => {
    if (!request) return undefined;
    const explicit = parseLocaleCookie(request.headers.get("cookie"));
    if (explicit) return explicit;
    // No explicit choice → the tenant default.
    const { resolveOrgDefaultLocale } = await import("./locale-default");
    return await resolveOrgDefaultLocale(request);
  },
});
