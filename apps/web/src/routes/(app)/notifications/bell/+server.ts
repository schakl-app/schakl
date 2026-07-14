/**
 * The header bell's data path (issue #16).
 *
 * There is no browser-side API client (Golden Rule 6: the web talks to the API, and it does so
 * from the server), and the bell lives in the shell rather than on a page, so it has no form
 * action to post to. This is that seam: a thin, authenticated proxy the bell polls.
 *
 * `+server.ts` endpoints do **not** run the `(app)` layout's load, so the auth guard is repeated
 * here rather than inherited.
 *
 * GET   → `{ count, items }` — the unread total (from the API, never `items.length`) plus the last
 *         few unread rows for the popover. `count=false` on the list: its total is discarded
 *         (docs/PERFORMANCE.md).
 * POST  → mark every visible notification read, and hand back the emptied state.
 * PATCH → mark one notification read (issue #164): clicking a popover row clears just that one,
 *         via the same per-row `set_read` endpoint the inbox toggle already uses.
 */
import { error, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/** Enough to fill the popover; "view all" is one click away. */
const PREVIEW_LIMIT = 8;

export const GET: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const api = apiFor(event);

  const [unread, list] = await Promise.all([
    api.GET("/api/v1/notifications/unread-count"),
    api.GET("/api/v1/notifications", {
      params: { query: { limit: PREVIEW_LIMIT, unread: true, count: false } },
    }),
  ]);

  return json({ count: unread.data?.count ?? 0, items: list.data?.items ?? [] });
};

export const POST: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  await apiFor(event).POST("/api/v1/notifications/mark-all-read");
  return json({ count: 0, items: [] });
};

export const PATCH: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const { id } = (await event.request.json().catch(() => ({}))) as { id?: string };
  if (!id) throw error(400, "errors.required");
  await apiFor(event).PATCH("/api/v1/notifications/{notification_id}", {
    params: { path: { notification_id: id } },
    body: { read: true },
  });
  return json({ ok: true });
};
