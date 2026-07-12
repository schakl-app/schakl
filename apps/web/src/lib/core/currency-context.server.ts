/**
 * Server-only: carries the tenant currency resolved in `hooks.server.ts` down to the synchronous
 * `getCurrency()` used by `format.ts` during SSR (issue #124).
 *
 * Same rationale as `timezone-context.server.ts`: a module-level variable would be shared across
 * concurrent tenant renders on one server, so the value rides `AsyncLocalStorage` through the
 * hook's `resolve()` chain instead. Imported for its side effect from `hooks.server.ts`; must not
 * be imported from client code (`node:async_hooks`).
 */
import { AsyncLocalStorage } from "node:async_hooks";

import { registerServerCurrencyResolver } from "./currency";

const store = new AsyncLocalStorage<string | undefined>();

// Wire the shared, client-safe `getCurrency()` to read this request-scoped store on the server.
registerServerCurrencyResolver(() => store.getStore());

/** Run `fn` (the rest of the request) with `currency` visible to `getCurrency()` during SSR. */
export function withRequestCurrency<T>(currency: string | undefined, fn: () => T): T {
  return store.run(currency, fn);
}
