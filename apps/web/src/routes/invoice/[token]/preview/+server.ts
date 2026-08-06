import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The rendered invoice as HTML, addressed by its public token — what `DocumentFrame` frames.
 *
 * A proxy for the same reason the signed-in one is: the frame must be **same-origin** so the
 * page can measure and print it. The difference is what authenticates it — there is no session
 * to forward, only the token already in this route's own URL.
 *
 * `Referrer-Policy: no-referrer` is restated here rather than inherited. The token is a path
 * segment, so the browser would otherwise offer it in a `Referer` on anything the document
 * links to, and the app's default (`strict-origin-when-cross-origin`) still sends the full URL
 * to same-origin destinations. A credential must not travel in a header nobody is watching.
 */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/public/invoices/{token}/preview",
    { params: { path: { token: event.params.token } }, parseAs: "text" },
  );
  if (error || data == null) throw httpError(response?.status ?? 404);
  return new Response(data as string, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "content-security-policy":
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
      "x-frame-options": "SAMEORIGIN",
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex, nofollow, noarchive",
      "cache-control": "no-store",
    },
  });
};
