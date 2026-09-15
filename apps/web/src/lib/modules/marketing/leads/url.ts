/**
 * The leads dashboard's filters in the URL: `?f=service:autotransport&f=language:nl`.
 *
 * One repeatable parameter in the API's own `dimension:value` shape, so what the page posts is
 * what the link says (§9, the URL is the view) and a filter added tomorrow needs no new
 * parameter name. Several values of one dimension are OR-ed by the API; dimensions AND.
 */

export type LeadFilters = Record<string, string[]>;

export function filtersFromUrl(url: URL): LeadFilters {
  const out: LeadFilters = {};
  for (const raw of url.searchParams.getAll("f")) {
    const i = raw.indexOf(":");
    if (i <= 0) continue;
    const key = raw.slice(0, i);
    const value = raw.slice(i + 1);
    if (!value) continue;
    out[key] ??= [];
    if (!out[key].includes(value)) out[key].push(value);
  }
  return out;
}

/** The `f` values to append to a URL or send to the API. */
export function filterParams(filters: LeadFilters): string[] {
  return Object.entries(filters).flatMap(([dimension, values]) =>
    values.map((value) => `${dimension}:${value}`),
  );
}

export function appendFilters(params: URLSearchParams, filters: LeadFilters): void {
  for (const value of filterParams(filters)) params.append("f", value);
}
