import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by every time page (entry form comboboxes, report filters, name display).
 * Deliberately a layout load that never touches the URL: switching day/week only reruns the
 * page load, so these four API calls don't repeat on every tab click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [companies, projects, tasks, members, companyDefs, projectDefs] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0, meta: false } } }),
    api.GET("/api/v1/members/lookup"),
    // Custom-field definitions drive the quick-create dialogs (incl. required fields).
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefs.data ?? [],
    projectDefinitions: projectDefs.data ?? [],
    locale: event.locals.locale,
  };
};
