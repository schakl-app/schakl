import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The selected invoices as one zip of PDFs (#307) — the bulk bar's Download.
 *
 * The single-PDF proxy next door, taking a list instead of one id. A **GET**, like the API it
 * fronts: this is a read, so it must keep working while a licence is expired (a module goes
 * read-only, not gone), and it is a plain link in the bar rather than a click handler — which
 * only works if the whole path is a navigation.
 *
 * The API decides what this caller may take: an id outside their horizon is simply not in the
 * archive, and a selection that resolves to nothing is its 404, relayed here.
 */
export const GET = async (event: RequestEvent) => {
  const ids = event.url.searchParams.getAll("ids").filter(Boolean);
  if (ids.length === 0) throw httpError(400);

  const { data, error, response } = await apiFor(event).GET("/api/v1/invoicing/invoices/pdf", {
    params: { query: { ids } },
    parseAs: "stream",
  });
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "application/zip",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'attachment; filename="facturen.zip"',
    },
  });
};
