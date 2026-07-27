import { error as httpError } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { paths } from "$lib/core/api/schema";

import type { RequestEvent } from "./$types";

/**
 * Entity slugs with an export route. The type comes off the generated client, so listing one
 * the API does not have — or forgetting one it gained — is a build error rather than a 404 or
 * a missing button nobody notices.
 */
type EntityOf<T> = T extends `/api/v1/impex/${infer E}/export` ? E : never;
type ExportEntity = EntityOf<keyof paths>;

const EXPORTABLE = [
  "company",
  "contact",
  "project",
  "task",
  "time_entry",
  "subscription",
] as const satisfies readonly ExportEntity[];

type NoneMissing =
  Exclude<ExportEntity, (typeof EXPORTABLE)[number]> extends never
    ? true
    : "impex: an exportable entity is missing from EXPORTABLE";
const _noneMissing: NoneMissing = true;
void _noneMissing;

/**
 * Streams any entity's CSV export through the user's session (issue #77, settings hub).
 * A plain fetch rather than the typed client: the path is dynamic per entity, and the bytes
 * pass through untouched (the BOM survives — `Response.text()` would strip it).
 */
export const GET = async (event: RequestEvent) => {
  const entity = event.params.entity;
  if (!(EXPORTABLE as readonly string[]).includes(entity)) throw httpError(404);
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
