/**
 * The leave form's hour preview (#48).
 *
 * There is no browser-side API client (Golden Rule 6: the web talks to the API, and it does so
 * from the server), and the form needs the number *before* it submits — so this is that seam: a
 * thin, authenticated proxy the form calls as the user picks dates and times.
 *
 * The number it returns is the number the server will store. The browser no longer guesses it;
 * `suggestedHours()` is gone precisely because two implementations of one rule is one too many.
 *
 * One call per meaningful change, debounced in the form (docs/PERFORMANCE.md — count the calls
 * before adding them). `+server.ts` endpoints do not run the `(app)` layout's load, so the auth
 * guard is repeated here rather than inherited.
 */
import { error, json } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

interface Body {
  user_id?: string | null;
  start_date: string;
  start_time?: string | null;
  end_date: string;
  end_time?: string | null;
  leave_type_id?: string | null;
}

export const POST: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const body = (await event.request.json()) as Body;
  if (!body.start_date || !body.end_date)
    return json({ error: "errors.required" }, { status: 400 });

  const { data, error: apiError } = await apiFor(event).POST("/api/v1/leave/requests/preview", {
    body: {
      user_id: body.user_id || null,
      start_date: body.start_date,
      start_time: body.start_time || null,
      end_date: body.end_date,
      end_time: body.end_time || null,
      // Lets the preview report whether saving needs (re-)approval (#72), for the edit warning.
      leave_type_id: body.leave_type_id || null,
    },
  });
  // A span that lands on a Saturday is a message to render, not a failure to swallow: the form
  // shows it under a zeroed hours field instead of pretending the request is fine.
  if (apiError) return json({ error: apiErrorKey(apiError).key }, { status: 200 });
  return json({ preview: data });
};
