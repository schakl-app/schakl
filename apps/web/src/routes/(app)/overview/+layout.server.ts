import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

// The whole Overzicht section is a manager surface; lookups are shared by every subpage
// and don't rerun when filters (query params) change.
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "time.report.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [companies, projects, tasks, members, entryTypes] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0, meta: false } } }),
    api.GET("/api/v1/members/lookup"),
    // Entry-type labels for the report's type column/filter (#176) — inactive included so a
    // historical row still names its retired type.
    api.GET("/api/v1/time/entry-types", { params: { query: { include_inactive: true } } }),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    members: members.data ?? [],
    entryTypes: entryTypes.data ?? [],
  };
};
