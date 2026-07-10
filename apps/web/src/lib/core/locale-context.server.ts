/**
 * Server-only: carries the locale resolved for the current request from `hooks.server.ts` down
 * into the Paraglide `custom-vlotrDefault` strategy.
 *
 * Why a context store and not another lookup: the strategy only receives the `Request`, but the
 * preference lives on the user, and `hooks.server.ts` has *already* fetched that user for
 * `event.locals`. Re-fetching `/meta/me` inside the strategy would add a second API call to
 * every SSR render (docs/PERFORMANCE.md). `AsyncLocalStorage` propagates through the hook's
 * `resolve()` chain, so the value the hook computed is simply readable where it is needed.
 */
import { AsyncLocalStorage } from "node:async_hooks";

const store = new AsyncLocalStorage<string | undefined>();

/** Run `fn` (the rest of the request) with `locale` visible to the server strategy. */
export function withRequestLocale<T>(locale: string | undefined, fn: () => T): T {
  return store.run(locale, fn);
}

/** The locale `hooks.server.ts` resolved for this request, if it ran. */
export function getRequestLocale(): string | undefined {
  return store.getStore();
}
