import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The hours report's lookups. A layout load rather than the page's own because the report's
 * filters are query parameters, and a page load reruns on every one of them — six lookups per
 * filter change is the cost this file exists to avoid (docs/PERFORMANCE.md). The member lookup
 * comes from the section layout above, which every tab shares.
 */
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "time.report.read")) throw redirect(303, "/overview");
  const api = apiFor(event);
  const [companies, projects, tasks, taskStatuses, entryTypes] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    // No `hours=true` here, unlike the /time layout: this lookup only names projects for the
    // report's filters and its edit modal. The budget burn is an opt-in aggregate and the
    // report never draws one (docs/PERFORMANCE.md).
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, count: false } },
    }),
    api.GET("/api/v1/tasks", {
      params: { query: { limit: 200, offset: 0, meta: false, count: false, sort: "title" } },
    }),
    // The tenant's status vocabulary (#62): the lookup above names the task on every reported
    // row, finished ones included, so this is what tells the edit modal's picker which of them
    // are still worth offering.
    api.GET("/api/v1/tasks/statuses"),
    // Entry-type labels for the report's type column/filter (#176) — inactive included so a
    // historical row still names its retired type.
    api.GET("/api/v1/time/entry-types", { params: { query: { include_inactive: true } } }),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    taskStatuses: taskStatuses.data ?? [],
    entryTypes: entryTypes.data ?? [],
  };
};
