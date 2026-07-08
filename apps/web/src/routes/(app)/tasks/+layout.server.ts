import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by the tasks list, the card and the templates page. A layout load that
 * never touches the URL: filter/search navigation only reruns the page load, so these
 * four calls don't repeat on every keystroke or filter click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [companies, projects, labels, members] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks/labels"),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    labels: labels.data ?? [],
    members: members.data ?? [],
  };
};
