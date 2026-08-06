import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** Streams the invoice PDF for the holder of its public link (#304) — the signed-in proxy's
 *  pattern, authenticated by the token in this route's own URL instead of by a session. */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/public/invoices/{token}/pdf",
    { params: { path: { token: event.params.token } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 404);
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'attachment; filename="invoice.pdf"',
      // The token is in the path, so it must not ride out in a `Referer` — and a downloaded
      // invoice must not be indexable if the URL ever reaches a crawler.
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex, nofollow, noarchive",
      "cache-control": "no-store",
    },
  });
};
