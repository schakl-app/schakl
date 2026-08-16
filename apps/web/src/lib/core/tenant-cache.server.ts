/**
 * Last-known tenant branding, per hostname — read by exactly one caller (Golden Rule 4).
 *
 * This is **not** a performance cache. `hooks.server.ts` fetches `/meta/tenant` on every request
 * and keeps doing so; nothing here is ever served to a request that could have asked. Its only
 * consumer is the outage path: when the API cannot be reached, the branded maintenance page has
 * to come from somewhere, and the only alternative is a stranger's colours on the agency's
 * domain — which reads as "you are on the wrong site" at the exact moment a client is already
 * unsure whether something is wrong.
 *
 * Consequences of that framing:
 *   - it is per **process**, so a fresh replica has nothing until it has served one good request.
 *     That is the honest trade: a shared store would be Redis, which is behind the same failure
 *     the page exists to survive.
 *   - it is keyed by hostname, because that is what resolves a tenant (CLAUDE.md §5/§7) and a
 *     cloud process serves many.
 *   - it is bounded and never expires. Stale branding on a maintenance page costs nothing; an
 *     unbounded map on a multi-tenant box that gets scanned for hostnames is a slow leak.
 */
import type { OrgTheme } from "./theme";

/** Comfortably more tenants than one process serves, small enough to be free. */
const MAX_HOSTS = 64;

const cache = new Map<string, OrgTheme>();

/** Record a tenant that resolved. Only branding is kept — the rest is re-fetched, always. */
export function rememberTheme(host: string | null | undefined, theme: OrgTheme): void {
  if (!host || !theme.resolved) return; // an unresolved host has no branding to remember
  // Re-insert so the eviction below drops the least recently *seen* host, not the oldest one:
  // on a cloud box the quiet tenants should go first, and Map iterates in insertion order.
  cache.delete(host);
  cache.set(host, theme);
  while (cache.size > MAX_HOSTS) {
    const oldest = cache.keys().next();
    if (oldest.done) break;
    cache.delete(oldest.value);
  }
}

/** The branding this host last resolved to, or `null` if this process has never seen it. */
export function lastKnownTheme(host: string | null | undefined): OrgTheme | null {
  if (!host) return null;
  const theme = cache.get(host);
  return theme ?? null;
}

/** Test seam — the module is process-global by design, so a test has to be able to reset it. */
export function clearThemeCache(): void {
  cache.clear();
}
