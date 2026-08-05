import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** The rendered quote as HTML — the invoice preview proxy's twin; see it for the why. */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/quotes/{quote_id}/preview",
    { params: { path: { quote_id: event.params.id } }, parseAs: "text" },
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
