/**
 * The tenant's currency (issue #124, CLAUDE.md §8), resolved isomorphically.
 *
 * A business fact of the org — you invoice and report in one currency — so it rides the same
 * plumbing as the timezone, not the per-user format prefs (#13):
 * - **Server:** `hooks.server.ts` resolves the tenant currency per request and wraps `resolve()`
 *   in `withRequestCurrency` (AsyncLocalStorage) — concurrent tenants never share module state.
 * - **Client:** the code is stamped on `<html data-currency>` and read from `document`.
 *
 * Kept free of server-only imports so importing it into `format.ts` never pulls `node:*` into
 * the browser bundle. The server half lives in `currency-context.server.ts`.
 */

// Neutral fallback before/without a resolved tenant — matches the API's own default.
export const DEFAULT_CURRENCY = "EUR";

/** True for an ISO 4217 code this runtime can format — the client-side guard for a stray value. */
export function isValidCurrency(code: string | null | undefined): code is string {
  if (!code || !/^[A-Z]{3}$/.test(code)) return false;
  try {
    new Intl.NumberFormat("en", { style: "currency", currency: code });
    return true;
  } catch {
    return false;
  }
}

// Filled in by `currency-context.server.ts` (server only); stays null in the browser bundle.
let serverResolver: (() => string | undefined) | null = null;

/** Registered once, server-side, from `currency-context.server.ts`. Not for app code. */
export function registerServerCurrencyResolver(fn: () => string | undefined): void {
  serverResolver = fn;
}

/** The currency to format in right now: the tenant's, falling back to the default. */
export function getCurrency(): string {
  const raw =
    typeof document !== "undefined"
      ? document.documentElement.dataset.currency
      : serverResolver?.();
  return isValidCurrency(raw) ? raw : DEFAULT_CURRENCY;
}
