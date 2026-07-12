/**
 * The tenant's display timezone (CLAUDE.md §8), resolved isomorphically.
 *
 * `format.ts` is synchronous and runs during SSR *and* on the client, so it needs the current
 * zone the same way it needs the locale. This mirrors the locale plumbing:
 * - **Server:** `hooks.server.ts` resolves the tenant zone per request and wraps `resolve()` in
 *   `withRequestTimezone` (AsyncLocalStorage) — so concurrent tenants never share module state.
 * - **Client:** the zone is stamped on `<html data-timezone>` and read from `document`.
 *
 * There is no per-user override yet (the shipped model is one agency in one zone, CLAUDE.md §5);
 * the seam is here so adding one later changes only the resolver, not every caller.
 *
 * Kept free of server-only imports so importing it into `format.ts` never pulls `node:*` into the
 * browser bundle. The server half lives in `timezone-context.server.ts`.
 */

// Neutral fallback before/without a resolved tenant — matches the API's own default.
export const DEFAULT_TIMEZONE = "Europe/Amsterdam";

/** True for an IANA zone name this runtime can format in — the client-side guard for a stray value. */
export function isValidTimeZone(name: string | null | undefined): name is string {
  if (!name) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

// Filled in by `timezone-context.server.ts` (server only); stays null in the browser bundle.
let serverResolver: (() => string | undefined) | null = null;

/** Registered once, server-side, from `timezone-context.server.ts`. Not for app code. */
export function registerServerTimezoneResolver(fn: () => string | undefined): void {
  serverResolver = fn;
}

/** The zone to format in right now: the request's tenant zone, falling back to the default. */
export function getTimeZone(): string {
  const raw =
    typeof document !== "undefined"
      ? document.documentElement.dataset.timezone
      : serverResolver?.();
  return isValidTimeZone(raw) ? raw : DEFAULT_TIMEZONE;
}
