import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The minutes as HTML — the *same* artefact the PDF prints (`render_meeting_html`), so what a
 * reader checks on screen is what the download contains. Opened in its own tab by the export
 * dialog's "preview", with the ticked chapters as `?sections=`.
 */
export const GET = async (event: RequestEvent) => {
  const sections = event.url.searchParams.get("sections");
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/meetings/{meeting_id}/preview",
    {
      params: { path: { meeting_id: event.params.id }, query: sections ? { sections } : {} },
      parseAs: "text",
    },
  );
  if (error || data === undefined) throw httpError(response?.status ?? 500);
  return new Response(String(data), {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "content-security-policy":
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
      "x-robots-tag": "noindex, nofollow",
    },
  });
};
