import { error as httpError, json } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { RequestEvent } from "./$types";

/**
 * Upload a template's background mark through the storage core (#123).
 *
 * Multipart goes through a plain fetch — the typed client has no multipart serializer — with
 * the same cookie and tenant host the client would send, which is the branding upload's
 * pattern. Deliberately **not** `entity_type=branding`: that type is served without a session
 * (the login screen renders it), and an invoice's letterhead is not something to publish
 * anonymously on the org's domain. It is an ordinary tenant file, read back by the renderer
 * through the org-scoped repository.
 */
export const POST = async (event: RequestEvent) => {
  const incoming = await event.request.formData();
  const upload = incoming.get("file");
  if (!(upload instanceof File)) throw httpError(400);

  const body = new FormData();
  body.append("file", upload, upload.name);
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/files?entity_type=invoicing_template`, {
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
