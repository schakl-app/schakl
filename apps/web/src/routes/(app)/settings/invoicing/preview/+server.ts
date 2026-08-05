import { error as httpError, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Render a sample document with an unsaved template config — the editor's live preview.
 *
 * A proxy so the frame stays same-origin (it has to be measurable and printable) and so the
 * session cookie never leaves the server. Authorization is the API's: this forwards the
 * caller's session and returns whatever it decides, including the 403 a caller without
 * `invoicing.template.author` gets for sending HTML.
 */
export const POST = async (event: RequestEvent) => {
  const body = await event.request.json();
  const { data, error, response } = await apiFor(event).POST(
    "/api/v1/invoicing/templates/preview",
    { body, parseAs: "text" },
  );
  if (error || data == null) {
    // The render errors the author needs to see (a Jinja syntax error) come back as a 422
    // envelope; pass the body through so the editor can name the line rather than shrug.
    if (response?.status === 422) return json(error ?? {}, { status: 422 });
    throw httpError(response?.status ?? 500);
  }
  return new Response(data as string, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      // Stricter than the page shell's in every direction but one: the document is framed
      // by its own app, so `frame-ancestors 'self'` — see `handleSecurityHeaders`.
      "content-security-policy":
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
      "x-frame-options": "SAMEORIGIN",
      "cache-control": "no-store",
    },
  });
};
