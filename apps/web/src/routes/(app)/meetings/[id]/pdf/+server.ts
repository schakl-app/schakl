import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Streams the minutes PDF through the caller's own session (the invoice-PDF pattern), with the
 * chapters the download dialog ticked riding along as `?sections=`.
 *
 * No permission check here on purpose: the API's read declares it and the row lookup is
 * tenant-scoped, so re-deriving either in the proxy is how the two ends drift (§15, #285).
 */
export const GET = async (event: RequestEvent) => {
  const sections = event.url.searchParams.get("sections");
  const { data, error, response } = await apiFor(event).GET("/api/v1/meetings/{meeting_id}/pdf", {
    params: {
      path: { meeting_id: event.params.id },
      query: sections ? { sections } : {},
    },
    parseAs: "stream",
  });
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "application/pdf",
      "content-disposition":
        response.headers.get("content-disposition") ?? 'attachment; filename="notulen.pdf"',
    },
  });
};
