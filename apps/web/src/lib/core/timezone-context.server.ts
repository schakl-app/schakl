/**
 * Server-only: carries the tenant timezone resolved in `hooks.server.ts` down to the synchronous
 * `getTimeZone()` used by `format.ts` during SSR.
 *
 * Same rationale as `locale-context.server.ts`: a module-level variable would be shared across
 * concurrent tenant renders on one server, so the value rides `AsyncLocalStorage` through the
 * hook's `resolve()` chain instead. Imported for its side effect from `hooks.server.ts`; must not
 * be imported from client code (`node:async_hooks`).
 */
import { AsyncLocalStorage } from "node:async_hooks";

import { registerServerTimezoneResolver } from "./timezone";

const store = new AsyncLocalStorage<string | undefined>();

// Wire the shared, client-safe `getTimeZone()` to read this request-scoped store on the server.
registerServerTimezoneResolver(() => store.getStore());

/** Run `fn` (the rest of the request) with `timezone` visible to `getTimeZone()` during SSR. */
export function withRequestTimezone<T>(timezone: string | undefined, fn: () => T): T {
  return store.run(timezone, fn);
}
