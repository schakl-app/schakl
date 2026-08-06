import { error as httpError, json } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { RequestEvent } from "./$types";

/**
 * Upload a report template's cover image through the storage core.
 *
 * Multipart goes through a plain fetch — the typed client has no multipart serializer — with
 * the same cookie and tenant host the client would send, which is the invoice letterhead's
 * pattern and the branding upload's before that.
 *
 * Deliberately **not** `entity_type=branding`: that type is served without a session (the
 * login screen renders it), and a photograph on the front of a client's report is not
 * something to publish anonymously on the org's domain. It is an ordinary tenant file, read
 * back by the renderer through the org-scoped repository and inlined as a `data:` URI —
 * which is also why nothing here ever hands the document a URL.
 */
export const POST = async (event: RequestEvent) => {
  const incoming = await event.request.formData();
  const upload = incoming.get("file");
  if (!(upload instanceof File)) throw httpError(400);

  const body = new FormData();
  body.append("file", upload, upload.name);
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/files?entity_type=reporting_template`, {
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
