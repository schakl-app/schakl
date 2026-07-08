/**
 * Server-only: resolve a tenant's **default** display locale (CLAUDE.md §7, §8).
 *
 * Used by the Paraglide `custom-vlotrDefault` server strategy when the visitor has made no
 * explicit choice (no `PARAGLIDE_LOCALE` cookie). It reads `org_settings.default_locale` via
 * the public `/meta/tenant` endpoint, resolving the tenant from the request's forwarded host.
 *
 * This module imports the API client (which reads private env), so it must only ever be
 * loaded on the server — the strategy `import()`s it lazily from inside its `isServer` branch.
 */
import { createApiClient } from "./api/client";

export async function resolveOrgDefaultLocale(request: Request): Promise<string | undefined> {
  try {
    const client = createApiClient({
      fetch,
      cookie: request.headers.get("cookie"),
      host: request.headers.get("x-forwarded-host") ?? request.headers.get("host"),
    });
    const { data } = await client.GET("/api/v1/meta/tenant");
    return data?.default_locale ?? undefined;
  } catch {
    // Never let locale resolution break rendering; fall through to baseLocale.
    return undefined;
  }
}
