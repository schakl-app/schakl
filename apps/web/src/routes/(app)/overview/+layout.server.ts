import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

// The whole Overzicht section is a manager surface; lookups are shared by every subpage
// and don't rerun when filters (query params) change.
export const load: LayoutServerLoad = async (event) => {
  // The Overzicht section holds several reports; a manager reaches it holding any one of them
  // (the time report, or — epic #134 — the marketing overview). Each subpage re-guards its own.
  if (
    !can(event.locals.user, "time.report.read") &&
    !can(event.locals.user, "marketing.report.read")
  ) {
    throw redirect(303, "/");
  }
  const api = apiFor(event);
  const [companies, projects, tasks, members, entryTypes] = await Promise.all([
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
