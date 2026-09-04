import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** Streams the invoice PDF for the holder of its public link (#304) — the signed-in proxy's
 *  pattern, authenticated by the token in this route's own URL instead of by a session.
 *  `?inline=1` shows it instead of saving it: the page frames an imported invoice's original. */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/public/invoices/{token}/pdf",
    { params: { path: { token: event.params.token } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 404);
  const disposition =
    response.headers.get("content-disposition") ?? 'attachment; filename="invoice.pdf"';
  const inline = event.url.searchParams.get("inline") === "1";
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition": inline ? disposition.replace(/^attachment/, "inline") : disposition,
      "content-security-policy": "frame-ancestors 'self'",
      "x-frame-options": "SAMEORIGIN",
      // The token is in the path, so it must not ride out in a `Referer` — and a downloaded
      // invoice must not be indexable if the URL ever reaches a crawler.
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex, nofollow, noarchive",
      "cache-control": "no-store",
    },
  });
};
