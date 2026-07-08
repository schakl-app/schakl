import { redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

// The whole Overzicht section is a manager surface; lookups are shared by every subpage
// and don't rerun when filters (query params) change.
export const load: LayoutServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const api = apiFor(event);
  const [companies, projects, tasks, members] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0, meta: false } } }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    members: members.data ?? [],
  };
};
