import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by every time page (entry form comboboxes, report filters, name display).
 * Deliberately a layout load that never touches the URL: switching day/week only reruns the
 * page load, so these four API calls don't repeat on every tab click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [companies, projects, tasks, members, companyDefs, projectDefs, prefs] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
    // `hours=true` (#112): the budget burn per project rides the lookup this layout already
    // makes — one grouped query server-side, zero extra API calls — so the entry form can
    // show hours-left for the project being logged against.
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, count: false, hours: true } },
    }),
    api.GET("/api/v1/tasks", {
      params: { query: { limit: 200, offset: 0, meta: false, count: false } },
    }),
    api.GET("/api/v1/members/lookup"),
    // Custom-field definitions drive the quick-create dialogs (incl. required fields).
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
    // Personal timesheet view preference (7-day vs Mon–Fri); URL-independent so it doesn't
    // refetch on day/week navigation.
    api.GET("/api/v1/prefs"),
  ]);
  const weekView = (prefs.data?.prefs as { time?: { week_view?: string } } | undefined)?.time
    ?.week_view;
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefs.data ?? [],
    projectDefinitions: projectDefs.data ?? [],
    weekView: weekView === "work" ? "work" : "full",
    // Money on the time page is manager-gated (#112); the hours bar stays team-visible.
    canSeeBudgetMoney: can(event.locals.user, "time.report.read"),
    locale: event.locals.locale,
  };
};
