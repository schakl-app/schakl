/**
 * Server-only: carries the per-user format choice resolved in `hooks.server.ts` down to the
 * synchronous `getClock()` / `getDateFormat()` used by `format.ts` during SSR.
 *
 * Same rationale as `timezone-context.server.ts`: a module-level variable would be shared across
 * concurrent user renders on one server, so the value rides `AsyncLocalStorage` through the hook's
 * `resolve()` chain instead. Imported for its side effect from `hooks.server.ts`; must not be
 * imported from client code (`node:async_hooks`).
 */
import { AsyncLocalStorage } from "node:async_hooks";

import { type FormatPrefs, registerServerFormatResolver } from "./dateformat";

const store = new AsyncLocalStorage<FormatPrefs | undefined>();

// Wire the shared, client-safe read seam to read this request-scoped store on the server.
registerServerFormatResolver(() => store.getStore());

/** Run `fn` (the rest of the request) with `format` visible to the read seam during SSR. */
export function withRequestFormat<T>(format: FormatPrefs | undefined, fn: () => T): T {
  return store.run(format, fn);
}
