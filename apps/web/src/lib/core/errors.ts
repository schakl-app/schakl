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
