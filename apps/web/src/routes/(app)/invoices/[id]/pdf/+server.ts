import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Streams the invoice PDF through the user's session (the UBL proxy's pattern).
 *
 * `?inline=1` asks for the same bytes to be *shown* rather than saved: the detail page frames
 * an imported invoice's original this way (`PdfFrame`). The disposition is the only header
 * that changes — the filename the API chose stays on it, so "save as" from inside the viewer
 * still names the file after the invoice.
 */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/invoices/{invoice_id}/pdf",
    { params: { path: { invoice_id: event.params.id } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  const disposition =
    response.headers.get("content-disposition") ?? 'attachment; filename="invoice.pdf"';
  const inline = event.url.searchParams.get("inline") === "1";
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition": inline ? disposition.replace(/^attachment/, "inline") : disposition,
      // Framed by its own app and nowhere else — the preview proxy's rule.
      "content-security-policy": "frame-ancestors 'self'",
      "x-frame-options": "SAMEORIGIN",
      "cache-control": "no-store",
    },
  });
};
