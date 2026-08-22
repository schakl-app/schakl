/**
 * Client-callable proxy for the connect dialog's website select (#399).
 *
 * The same shape as `../companies/+server.ts` and `../accounts/+server.ts` beside it, and for
 * the same two reasons. It is **lazy** — a site select most visits never see must not cost a
 * read on every render of `/marketing` (docs/PERFORMANCE.md) — and it goes through the
 * request-scoped typed client rather than being fetched from the browser as `/api/v1/...`
 * directly, which only resolves where the edge forwards `/api/`: production and not `vite dev`
 * (CLAUDE.md §12, a route the edge does not forward is a route nobody has).
 *
 * It reads marketing's own endpoint rather than `/websites`, because the two are gated
 * differently: this question is part of the link the caller is already allowed to make
 * (`marketing.link.manage`), and requiring `websites.website.read` on top would leave the Rank
 * Math picker refusing for exactly the person the agency put in charge of connecting sources.
 */
import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  const company_id = event.url.searchParams.get("company") ?? "";
  if (!company_id) return json({ items: [] });
  const { data } = await apiFor(event).GET("/api/v1/marketing/companies/{company_id}/websites", {
    params: { path: { company_id } },
  });
  return json({ items: (data ?? []).map((w) => ({ id: w.id, name: w.name })) });
};
