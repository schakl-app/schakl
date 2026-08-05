import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The rendered invoice as HTML, through the user's session — what `DocumentFrame` frames.
 *
 * A proxy rather than a direct API URL because the frame must be **same-origin**: that is
 * what lets the page measure the document's height and print it, and it keeps the API's
 * session cookie where it belongs. The API's own CSP travels with the response, so the
 * document stays a scriptless page that can only load `data:` images.
 *
 * The same bytes `/pdf` prints. That is the point of the endpoint existing at all.
 */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/invoices/{invoice_id}/preview",
    { params: { path: { invoice_id: event.params.id } }, parseAs: "text" },
  );
  if (error || data == null) throw httpError(response?.status ?? 500);
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
