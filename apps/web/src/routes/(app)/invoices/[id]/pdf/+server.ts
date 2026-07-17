import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** Streams the invoice PDF through the user's session (the UBL proxy's pattern). */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/invoices/{invoice_id}/pdf",
    { params: { path: { invoice_id: event.params.id } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'attachment; filename="invoice.pdf"',
    },
  });
};
