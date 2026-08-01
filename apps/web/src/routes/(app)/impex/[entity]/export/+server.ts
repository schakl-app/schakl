import { error as httpError } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { EXPORT_FILTERS, isExportable } from "$lib/core/impex/actions.server";

import type { RequestEvent } from "./$types";

/**
 * The CSV download for **every** entity (issue #77, second round).
 *
 * One proxy rather than one per list: the browser cannot reach the API host directly, and the
 * only thing that differed between the three hand-written proxies this replaces was the slug
 * and which filters they remembered to forward — which is exactly the kind of difference that
 * quietly becomes "the domains export ignores the search box".
 *
 * A plain fetch rather than the typed client: the path is dynamic per entity, and the bytes
 * pass through untouched so the UTF-8 BOM survives (`Response.text()` would strip it, and
 * Excel would then mangle every accent). The API decides what the caller may take out — this
 * relays its status, it does not judge.
 */
export const GET = async (event: RequestEvent) => {
  const entity = event.params.entity;
  if (!isExportable(entity)) throw httpError(404);

  // Only the known filter vocabulary is forwarded, in a fixed order: an unknown query param
  // would be rejected by the API's generated signature anyway, and forwarding the raw query
  // string would let a stray `?dry_run=` or a tracking param ride along.
  const incoming = event.url.searchParams;
  const query = new URLSearchParams();
  for (const key of EXPORT_FILTERS) {
    const value = incoming.get(key);
    if (value) query.set(key, value);
  }

  const suffix = query.toString();
  const response = await event.fetch(
    `${apiBaseUrl()}/api/v1/impex/${entity}/export${suffix ? `?${suffix}` : ""}`,
    {
      headers: {
        cookie: event.request.headers.get("cookie") ?? "",
        "x-forwarded-host": event.request.headers.get("host") ?? "",
      },
    },
  );
  if (!response.ok || !response.body) throw httpError(response.status);
  return new Response(response.body, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition":
        response.headers.get("content-disposition") ??
        `attachment; filename="${entity}-export.csv"`,
    },
  });
};
