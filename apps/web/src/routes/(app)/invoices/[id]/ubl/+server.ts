import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** Streams the invoice's UBL 2.1 XML through the user's session (the impex-export pattern):
 * the browser can't reach the API host directly, so downloads proxy via the web app. */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/invoicing/invoices/{invoice_id}/ubl",
    { params: { path: { invoice_id: event.params.id } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "application/xml",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'attachment; filename="invoice.xml"',
    },
  });
};
