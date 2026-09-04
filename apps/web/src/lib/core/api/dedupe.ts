import type { ApiClient } from "$lib/core/api/client";

/**
 * An API client that answers identical `GET`s once per render (docs/PERFORMANCE.md).
 *
 * A load function composed from independent pieces — dashboard widgets from the registry, a
 * detail page's panels, a section layout plus the page beneath it — will ask for the same
 * digest more than once, because no piece knows about the others. Coalescing here keeps the
 * composition honest (each piece still requests what it needs) without paying for the
 * duplicates: identical calls share one promise, so one HTTP request, one `require_context`,
 * one set of queries.
 *
 * **Per render, never module scope.** The cache lives inside the returned proxy and dies with
 * it, so it can never serve one user's rows to the next request. Build it inside `load`:
 *
 * ```ts
 * const api = dedupeGets(apiFor(event));
 * ```
 *
 * Only `GET` is coalesced — a repeated write is a repeated write, and silently collapsing two
 * of them would be a correctness bug, not an optimisation. The key is the full argument list,
 * so a different path, query or header is a different request.
 */
export function dedupeGets(api: ApiClient): ApiClient {
  const cache = new Map<string, Promise<unknown>>();
  // Widened through `unknown` at both ends, on purpose. Handing `api.GET` to `Reflect.apply`
  // makes TypeScript compare its overload over *every* path in the generated client against
  // `Function`, and at ~630 operations that comparison exceeds the compiler's instantiation
  // depth ("Type instantiation is excessively deep") — the thirteen Search Console routes were
  // the ones that tipped it. The runtime is identical; only the proof is skipped.
  const original = api.GET as unknown as (...args: unknown[]) => Promise<unknown>;
  const get = ((...args: unknown[]) => {
    const key = JSON.stringify(args);
    let request = cache.get(key);
    if (!request) {
      request = original.apply(api, args);
      cache.set(key, request);
    }
    return request;
  }) as unknown as ApiClient["GET"];
  return new Proxy(api, {
    get(target, property, receiver) {
      return property === "GET" ? get : Reflect.get(target, property, receiver);
    },
  });
}
