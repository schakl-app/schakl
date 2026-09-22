import { error as httpError } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/** The transcript as a file (`?format=txt|md|srt|vtt`), streamed through the caller's session. */
export const GET = async (event: RequestEvent) => {
  const format = event.url.searchParams.get("format") ?? "txt";
  const { data, error, response } = await apiFor(event).GET(
    "/api/v1/meetings/{meeting_id}/transcript",
    {
      params: { path: { meeting_id: event.params.id }, query: { format } },
      parseAs: "stream",
    },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return new Response(data, {
    headers: {
      "content-type": response.headers.get("content-type") ?? "text/plain; charset=utf-8",
      "content-disposition":
        response.headers.get("content-disposition") ??
        `attachment; filename="transcript.${format}"`,
    },
  });
};
