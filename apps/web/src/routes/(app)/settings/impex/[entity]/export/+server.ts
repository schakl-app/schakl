import { error as httpError } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { RequestEvent } from "./$types";

/** Entity slugs with an export route — mirrors the API's impex registry. */
const EXPORTABLE = new Set([
  "company",
  "contact",
  "project",
  "task",
  "time_entry",
  "subscription",
]);

/**
 * Streams any entity's CSV export through the user's session (issue #77, settings hub).
 * A plain fetch rather than the typed client: the path is dynamic per entity, and the bytes
 * pass through untouched (the BOM survives — `Response.text()` would strip it).
 */
export const GET = async (event: RequestEvent) => {
  const entity = event.params.entity;
  if (!EXPORTABLE.has(entity)) throw httpError(404);
  const response = await event.fetch(`${apiBaseUrl()}/api/v1/impex/${entity}/export`, {
    headers: {
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
  });
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
