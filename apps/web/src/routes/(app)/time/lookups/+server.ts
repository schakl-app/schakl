/**
 * The entry form's comboboxes, fetched the moment somebody opens a log-hours dialog (#402).
 *
 * `/time` gets these four from its layout load, because that page *is* the entry form. A record's
 * page is not: the client hub draws fourteen panels and most visits never log an hour, so paying
 * for four list reads on every open to serve a dialog nobody asked for is the cost `docs/
 * PERFORMANCE.md` rejects everywhere else (#314's rule — the offer suggests from what the screen
 * already has, and anything more is fetched when it is wanted).
 *
 * Deliberately the **same four lists** the time layout loads rather than a set narrowed to the
 * client the dialog was opened from. A task carries `company_id` independently of `project_id`
 * (#363) and the API's task filter is a plain column match, so a task hanging off one of this
 * client's projects with no client of its own would simply be missing from a narrowed lookup —
 * and the picker would be quietly wrong rather than visibly empty. The client is preselected by
 * the dialog instead, which is a default the user can see and change.
 *
 * `+server.ts` endpoints don't run the `(app)` layout, so the auth guard repeats
 * (`calendar/schedulable`, same shape).
 */
import { error, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const api = apiFor(event);
  const [companies, projects, tasks, taskStatuses] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    // `hours=true` on both lookups: the budget burn the form draws beside a picked project (#112)
    // and a picked task (#313) rides these reads, so showing it costs no extra call.
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, count: false, hours: true } },
    }),
    api.GET("/api/v1/tasks", {
      params: {
        query: { limit: 200, offset: 0, meta: false, count: false, hours: true, sort: "title" },
      },
    }),
    // The tenant's own status vocabulary (#62) — what tells an open task from a finished one.
    api.GET("/api/v1/tasks/statuses"),
  ]);
  return json({
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    taskStatuses: taskStatuses.data ?? [],
  });
};
