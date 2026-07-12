/**
 * Autosave seam for the in-progress registration (#44).
 *
 * Deliberately NOT a form action: `use:enhance` reruns the page load, so every keystroke batch
 * would refetch the week, the day, the timer and the leave feed. This thin authenticated proxy
 * invalidates nothing — the client fetches it debounced, flushes it on navigation, and beacons
 * it on tab close. `+server.ts` endpoints do not run the `(app)` layout's load, so the auth
 * guard is repeated here rather than inherited (same as the leave preview seam).
 *
 * POST is the same upsert as PUT because `navigator.sendBeacon` can only POST.
 */
import { error, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

interface Body {
  date?: string;
  payload?: Record<string, unknown>;
}

const upsert: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const body = (await event.request.json()) as Body;
  if (!body?.date) return json({ error: "errors.required" }, { status: 400 });
  const { data, error: apiError } = await apiFor(event).PUT("/api/v1/time/drafts/{entry_date}", {
    params: { path: { entry_date: body.date } },
    body: (body.payload ?? {}) as never,
  });
  if (apiError) return json({ saved: false }, { status: 400 });
  return json({ saved: true, updated_at: data?.updated_at ?? null });
};

export const PUT = upsert;
export const POST = upsert;

export const DELETE: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const body = (await event.request.json()) as Body;
  if (!body?.date) return json({ error: "errors.required" }, { status: 400 });
  const { error: apiError } = await apiFor(event).DELETE("/api/v1/time/drafts/{entry_date}", {
    params: { path: { entry_date: body.date } },
  });
  if (apiError) return json({ discarded: false }, { status: 400 });
  return json({ discarded: true });
};
