import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Streams the report PDF through the caller's own session (the invoice-PDF pattern).
 *
 * No permission check here on purpose: the API loads the report through the portal-aware
 * repository, so a client login gets exactly their own published client-facing reports and a
 * 404 for anything else — including the internal analysis. Re-deriving that rule in the proxy
 * is how the two ends drift (§15, #285).
 */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/reporting/reports/{report_id}/pdf",
    { params: { path: { report_id: event.params.id } }, parseAs: "stream" },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'inline; filename="report.pdf"',
    },
  });
};
