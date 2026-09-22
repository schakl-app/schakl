import { error as httpError, json } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { RequestEvent } from "./$types";

/**
 * Upload the minutes document's header image through the storage core (the report cover's
 * proxy). An ordinary tenant file — never `entity_type=branding`, which is served without a
 * session — read back by the renderer through the org-scoped repository and inlined as a
 * `data:` URI, which is why nothing here ever hands the document a URL.
 */
export const POST = async (event: RequestEvent) => {
  const incoming = await event.request.formData();
  const upload = incoming.get("file");
  if (!(upload instanceof File)) throw httpError(400);

  const body = new FormData();
  body.append("file", upload, upload.name);
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/files?entity_type=meeting_document`, {
    method: "POST",
    headers: {
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
    body,
  });
  if (!res.ok) throw httpError(res.status === 413 ? 413 : 422);
  const meta = (await res.json()) as { id: string };
  return json({ id: meta.id });
};
