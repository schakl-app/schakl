/**
 * Extract the API's error envelope (CLAUDE.md §9) from an openapi-fetch error.
 *
 * The API always returns `{ error: { code, message, fields? } }` where `message` is an i18n key,
 * but that envelope isn't described in the OpenAPI spec (which only documents the default
 * validation shape), so the generated error type is untyped for our purposes. This narrows it.
 */
export interface ApiError {
  key: string;
  fields?: Record<string, string>;
}

export function apiErrorKey(error: unknown, fallback = "errors.validation"): ApiError {
  const envelope = (error as { error?: { message?: string; fields?: Record<string, string> } })
    ?.error;
  return { key: envelope?.message ?? fallback, fields: envelope?.fields };
}

export interface Streamed<T> {
  /** What the read answered, or `null` when it did not. */
  data: T | null;
  /** An i18n key when it refused or could not be made at all; `null` when it answered. */
  errorKey: string | null;
}

/**
 * Settle a **streamed** API read into the one shape a page draws (docs/PERFORMANCE.md).
 *
 * "A streamed section's error streams with it" was written about the envelope above: a top-level
 * error key cannot be computed before the read answers, so one of them holds back a shell that
 * every other promise beside it was ready to render. That rule does not cover the *other*
 * refusal, and the other refusal is the one that happens during a redeploy — openapi-fetch lets
 * a `fetch` throw propagate, so an API that is restarting **rejects** the promise instead of
 * answering one with an envelope in it.
 *
 * A rejected streamed promise is not an error the section can draw. SvelteKit sends the shell,
 * then hands the browser a promise that rejects, and the `.then` that was going to clear the
 * pending flag never runs: the page sits on "Laden…" for as long as the tab is open, on a screen
 * whose whole design is that a refusal is a state it renders. It is docs/DEPLOY.md's rule inside
 * one page — the part that says what went wrong must not be behind the thing that went wrong.
 *
 * So the throw is folded into the envelope the answer arrives in. One shape, one branch, and no
 * way for a pending state to outlive the request that set it.
 *
 * @param promise an unawaited openapi-fetch call, returned from a server `load` to stream it.
 *   Typed by what it *has* rather than by its payload, because the call is often a conditional
 *   over several endpoints — the report page picks one of six — and a union of `FetchResponse`s
 *   has no payload type to infer. Name the shape with `streamed<Foo>(…)` where there is one.
 * @param fallback the key to show when the envelope names none — and the key for a throw, which
 *   carries no envelope at all. Stated rather than inherited: `apiErrorKey`'s own default is
 *   `errors.validation` ("check the fields you filled in"), which is the wrong sentence about a
 *   read and a badly wrong one about an API that is not answering.
 */
export function streamed<T = unknown>(
  promise: Promise<{ data?: unknown; error?: unknown }>,
  fallback = "errors.server",
): Promise<Streamed<T>> {
  return promise.then(
    (response) => ({
      data: (response.data ?? null) as T | null,
      errorKey: response.error ? apiErrorKey(response.error, fallback).key : null,
    }),
    // Never rethrown. By the time this arrives the shell is already on the wire, so throwing
    // would replace a rendered page with an error page halfway through delivering it.
    () => ({ data: null, errorKey: fallback }),
  );
}

/**
 * Unwrap a paged lookup response, logging a failed call instead of swallowing it.
 *
 * Lookup lists that feed pickers are non-fatal — the page still renders — but a silent
 * `?? []` makes a 403/422 indistinguishable from "no rows", which is how #116 shipped.
 */
export function lookupItems<T>(
  resp: { data?: { items?: T[] } | null; error?: unknown },
  label: string,
): T[] {
  if (resp.error) console.error(`lookup ${label} failed`, resp.error);
  return resp.data?.items ?? [];
}
