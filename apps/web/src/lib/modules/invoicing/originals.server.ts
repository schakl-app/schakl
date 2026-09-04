/**
 * Forwarding an original PDF to the API (the imported-invoice work, docs/INVOICING.md).
 *
 * The two attach routes are multipart, which the generated client types as a flat record of
 * strings — so the bytes go through `event.fetch` with the session cookie and the tenant host
 * carried by hand, the HR dossier upload's pattern. One helper, because a zip of originals on
 * the list and a single PDF on the detail page are the same request with a different path.
 *
 * The API is the authority on what a PDF is (type, magic bytes, size) and on which invoice a
 * file belongs to; this only relays its envelope, so a refusal lands as the key the API chose.
 */
import { apiBaseUrl } from "$lib/core/api/client";
import { apiErrorKey } from "$lib/core/errors";
import type { ApiEvent } from "$lib/core/session";

export async function postOriginal<T>(
  event: ApiEvent,
  path: string,
  file: File,
): Promise<{ data: T } | { error: string }> {
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await event.fetch(`${apiBaseUrl()}${path}`, {
    method: "POST",
    headers: {
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
    body,
  });
  if (res.status === 413) return { error: "errors.upload_too_large" };
  const payload = (await res.json().catch(() => null)) as T | null;
  if (!res.ok) return { error: apiErrorKey(payload).key };
  if (payload == null) return { error: "errors.validation" };
  return { data: payload };
}
