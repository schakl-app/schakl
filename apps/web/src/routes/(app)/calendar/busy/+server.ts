/**
 * "Wat staat er al?" — the calendars behind the scheduling dialog's conflict check.
 *
 * A thin proxy onto `GET /api/v1/tasks/schedules/busy`, for the same reason the recurrence
 * preview has one: a `fetch()` from the browser to `/api/v1/...` only resolves behind traefik,
 * so on every dev server the same call 404s against the SSR app and the dialog would read as
 * "everyone is free". Going through `apiFor` keeps the cookie and the tenant host attached and
 * the call inside the typed client (Golden Rule 6). `+server.ts` endpoints don't run the `(app)`
 * layout, so the auth guard repeats.
 *
 * Under `/calendar` rather than `/tasks`, because what it answers is a calendar question and the
 * dialog is mounted from the Agenda as well as from a task.
 */
import { error, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const date = event.url.searchParams.get("date") ?? "";
  const userIds = event.url.searchParams.getAll("user_ids").filter(Boolean);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || userIds.length === 0) {
    throw error(400, "errors.required");
  }
  const {
    data,
    error: apiError,
    response,
  } = await apiFor(event).GET("/api/v1/tasks/schedules/busy", {
    params: { query: { user_ids: userIds, date_from: date, date_to: date } },
  });
  // The status is passed through rather than flattened: "you may not see this person's
  // calendar" (403) and "the API is down" are different sentences for the dialog to say.
  if (apiError || !data) {
    throw error(
      response?.status ?? 502,
      response?.status === 403 ? "errors.forbidden" : "errors.validation",
    );
  }
  return json(data);
};
