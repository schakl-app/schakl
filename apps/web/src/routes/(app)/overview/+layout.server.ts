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
  // The report's edit modal reuses the entry form, so its optional subscription picker
  // needs the same lookup as the /time layout — only when the module is on and readable.
  const subscriptionsEnabled =
    (event.locals.theme?.enabledModules?.includes("subscriptions") ?? false) &&
    can(event.locals.user, "subscriptions.subscription.read");
  const [companies, projects, tasks, members, entryTypes, subscriptions] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0, meta: false } } }),
    api.GET("/api/v1/members/lookup"),
    // Entry-type labels for the report's type column/filter (#176) — inactive included so a
    // historical row still names its retired type.
    api.GET("/api/v1/time/entry-types", { params: { query: { include_inactive: true } } }),
    subscriptionsEnabled
      ? api.GET("/api/v1/subscriptions", {
          params: { query: { limit: 200, offset: 0, status: "active" } },
        })
      : Promise.resolve({ data: null }),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    members: members.data ?? [],
    entryTypes: entryTypes.data ?? [],
    subscriptions: subscriptions.data?.items ?? [],
  };
};
