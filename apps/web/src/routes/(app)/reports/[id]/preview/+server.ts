import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The report as HTML, for the review screen's iframe.
 *
 * The *same* artefact the PDF prints (`render_report_html`), which is the whole point: what a
 * reviewer approves on screen is what the client receives, and "the preview and the PDF
 * disagree" is not expressible (docs/INVOICING.md's rule, inherited with the engine).
 */
export const GET = async (event: RequestEvent) => {
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/reporting/reports/{report_id}/preview",
    { params: { path: { report_id: event.params.id } }, parseAs: "text" },
  );
  if (error || data === undefined) throw httpError(response?.status ?? 500);
  return new Response(String(data), {
    headers: {
      "content-type": "text/html; charset=utf-8",
      // The document is a standalone page rendered from tenant + Google data. It is framed by
      // our own review screen and nowhere else.
      "content-security-policy": "frame-ancestors 'self'",
      "x-robots-tag": "noindex, nofollow",
    },
  });
};
