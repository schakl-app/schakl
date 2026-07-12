import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * Streams the contacts CSV export (issue #77) through the user's session — see the companies
 * twin for why this proxies. Carries the list page's current filters through unchanged.
 */
export const GET = async (event: RequestEvent) => {
  const params = event.url.searchParams;
  const { data, error, response } = await apiFor(event).GET("/api/v1/impex/contact/export", {
    params: {
      query: {
        q: params.get("q") || undefined,
        company_id: params.get("company") || undefined,
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
        'attachment; filename="contact-export.csv"',
    },
  });
};
