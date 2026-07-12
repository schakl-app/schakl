/**
 * Shared form action for the CSV import flow (issue #77).
 *
 * Both list pages post the same shape: a `file` plus a `mode` submit button — `preview` runs
 * the API's dry run (the default), `commit` applies all-or-nothing. The uploaded file is
 * forwarded to the API as multipart through the typed client (Golden Rule 6); the API is the
 * authority on validation, this action only relays its report.
 */
import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor, type ApiEvent } from "$lib/core/session";

import type { components } from "$lib/core/api/schema";

export type ImportReport = components["schemas"]["ImportReport"];
export type ImportRowError = components["schemas"]["ImportRowError"];

type ImportPath = "/api/v1/impex/company/import" | "/api/v1/impex/contact/import";

export async function importCsvAction(event: ApiEvent, path: ImportPath) {
  const form = await event.request.formData();
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return fail(400, { impexError: "impex.errors.no_file" });
  }
  const commit = String(form.get("mode") ?? "") === "commit";

  const body = new FormData();
  body.append("file", file, file.name || "import.csv");
  const { data, error } = await apiFor(event).POST(path, {
    params: { query: { dry_run: !commit } },
    // The generated schema types the multipart body as `{ file: string }` (binary); hand the
    // real FormData straight to fetch so it sets the multipart boundary itself.
    body: body as unknown as { file: string },
    bodySerializer: (b: unknown) => b as FormData,
  });
  if (error || !data) return fail(400, { impexError: apiErrorKey(error).key });
  return { impex: data as ImportReport };
}
