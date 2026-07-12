import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Streams the companies CSV export (issue #77) through the user's session. The browser can't
 * reach the API host directly, so the download proxies via the web app; the list page's
 * Export link carries its current filters here and they pass through unchanged, so the file
 * holds exactly the filtered list on screen — the whole set, not the visible page.
 */
export const GET = async (event: RequestEvent) => {
  const params = event.url.searchParams;
  const { data, error, response } = await apiFor(event).GET("/api/v1/impex/company/export", {
    params: {
      query: {
        q: params.get("q") || undefined,
        status: params.get("status") || undefined,
        mine: params.get("mine") === "1" || undefined,
        sort: params.get("sort") || undefined,
      },
    },
    parseAs: "stream",
  });
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition":
        response.headers.get("content-disposition") ??
        'attachment; filename="company-export.csv"',
    },
  });
};
