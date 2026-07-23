/**
 * Schedulable tasks + lookups for the calendar "+" schedule modal (#188).
 *
 * The base calendar load stays lean (docs/PERFORMANCE.md) — this data is only needed once the
 * user actually opens the modal, so it is fetched here on demand rather than on every calendar
 * navigation. `+server.ts` endpoints don't run the `(app)` layout, so the auth guard repeats.
 */
import { error, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async (event) => {
  if (!event.locals.user) throw error(401, "errors.unauthorized");
  const api = apiFor(event);
  const [tasks, members, companies, projects] = await Promise.all([
    api.GET("/api/v1/tasks", {
      params: { query: { limit: 200, offset: 0, meta: false, count: false, sort: "title" } },
    }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, count: false } } }),
  ]);
  // Only open work is schedulable — you don't plan time for a finished task.
  const schedulable = (tasks.data?.items ?? []).filter((task) => !task.completed_at);
  return json({
    tasks: schedulable,
    members: members.data ?? [],
    companies: (companies.data?.items ?? []).map((c) => ({ id: c.id, name: c.name })),
    projects: (projects.data?.items ?? []).map((p) => ({ id: p.id, name: p.name })),
  });
};
