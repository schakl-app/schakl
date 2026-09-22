import { error as httpError, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Render the minutes document with unsaved settings — the editor's live preview (the reporting
 * template editor's proxy, one document family over). Same-origin so the frame is measurable;
 * the session cookie never leaves the server.
 */
export const POST = async (event: RequestEvent) => {
  const body = await event.request.json();
  const { data, error, response } = await apiFor(event).POST("/api/v1/meetings/settings/preview", {
    body,
    parseAs: "text",
  });
  if (error || data == null) {
    if (response?.status === 422) return json(error ?? {}, { status: 422 });
    throw httpError(response?.status ?? 500);
  }
  return new Response(data as string, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "content-security-policy":
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
      "x-frame-options": "SAMEORIGIN",
      "cache-control": "no-store",
    },
  });
};
