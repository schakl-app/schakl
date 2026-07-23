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
  // The optional subscription picker on the entry form (owner request): only fetched when
  // the module is on and the user may read agreements — otherwise the picker never renders.
  const subscriptionsEnabled =
    (event.locals.theme?.enabledModules?.includes("subscriptions") ?? false) &&
    can(event.locals.user, "subscriptions.subscription.read");
  const subscriptionsP = subscriptionsEnabled
    ? api.GET("/api/v1/subscriptions", {
        params: { query: { limit: 200, offset: 0, status: "active" } },
      })
    : Promise.resolve({ data: null });
  const [companies, projects, tasks, members, companyDefs, projectDefs, prefs, subscriptions] =
    await Promise.all([
      api.GET("/api/v1/companies", {
        params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
      }),
      // `hours=true` (#112): the budget burn per project rides the lookup this layout already
      // makes — one grouped query server-side, zero extra API calls — so the entry form can
      // show hours-left for the project being logged against.
      api.GET("/api/v1/projects", {
        params: { query: { limit: 200, offset: 0, count: false, hours: true } },
      }),
      api.GET("/api/v1/tasks", {
        params: { query: { limit: 200, offset: 0, meta: false, count: false, sort: "title" } },
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
      subscriptionsP,
    ]);
  const weekView = (prefs.data?.prefs as { time?: { week_view?: string } } | undefined)?.time
    ?.week_view;
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    subscriptions: subscriptions.data?.items ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefs.data ?? [],
    projectDefinitions: projectDefs.data ?? [],
    weekView: weekView === "work" ? "work" : "full",
    locale: event.locals.locale,
  };
};
