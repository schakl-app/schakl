import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

/**
 * "Volgende taak: za 13 sep 2026", for a rule that is still being typed (#335).
 *
 * A thin proxy onto `POST /api/v1/tasks/recurrence/preview`, and it exists because a `fetch()`
 * issued *from the browser* to `/api/v1/...` only resolves behind traefik: `vite.config.ts` has
 * no proxy, so on every dev server the same call 404s against the SSR app — a preview that
 * silently never appears, which reads as "this rule resolves to nothing" rather than as a broken
 * request. Going through `apiFor` also keeps the cookie and the tenant host attached and the call
 * inside the typed client (Golden Rule 6), rather than hand-assembling a request in a component.
 *
 * Scoped under the task rather than at `/tasks/…`, so it can never shadow the `[id]` route.
 * The task itself is not read here — the rule being previewed is the one on screen, not the one
 * stored — but the segment keeps the URL honest about where the call comes from.
 */
export const POST: RequestHandler = async (event) => {
  const body = await event.request.json();
  const { data, error } = await apiFor(event).POST("/api/v1/tasks/recurrence/preview", { body });
  // A refusal is a rule the API would refuse to store, which the editor says in words. The status
  // is passed through rather than flattened, so "invalid rule" and "you may not edit this" stay
  // different answers.
  if (error || !data) return json({ error: true }, { status: 422 });
  return json(data);
};
