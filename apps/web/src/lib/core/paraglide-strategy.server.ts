/**
 * Server half of the `custom-schaklDefault` Paraglide strategy (CLAUDE.md §8).
 *
 * The compiled strategy order is `cookie → custom-schaklDefault → baseLocale`. The intent:
 *   1. the user's saved preference (`users.locale`) wins — it follows them across devices;
 *   2. otherwise this browser's `PARAGLIDE_LOCALE` cookie (e.g. a signed-out visitor's choice);
 *   3. otherwise the **tenant's** default locale (`org_settings.default_locale`) — the app
 *      ships showing `nl` even though `en` is the source locale;
 *   4. `baseLocale` (`en`) is the last-resort fallback.
 *
 * Steps 1–3 are decided once per request by `hooks.server.ts`, which already holds the user and
 * the tenant, and reach us through `getRequestLocale()`. The cookie and tenant lookups below are
 * the fallback for any request that did not pass through that hook.
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
import { getRequestLocale } from "./locale-context.server";
import { defineCustomServerStrategy } from "$lib/paraglide/runtime";

defineCustomServerStrategy("custom-schaklDefault", {
  getLocale: async (request?: Request): Promise<string | undefined> => {
    const resolved = getRequestLocale();
    if (resolved) return resolved;

    if (!request) return undefined;
    const explicit = parseLocaleCookie(request.headers.get("cookie"));
    if (explicit) return explicit;
    // No explicit choice → the tenant default.
    const { resolveOrgDefaultLocale } = await import("./locale-default");
    return await resolveOrgDefaultLocale(request);
  },
});
