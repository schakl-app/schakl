import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by the tasks list, the card and the templates page. A layout load that
 * never touches the URL: filter/search navigation only reruns the page load, so these
 * four calls don't repeat on every keystroke or filter click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  // How many of the viewer's mails to the task address wait for a client — the count the
  // E-mailinbox tab is drawn with, only when it is not zero. A client login never mails the
  // address and holds no create permission, so it never pays for the read.
  const canCreate = can(event.locals.user, "tasks.task.create");
  const [companies, projects, labels, statuses, members, intake] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, count: false } },
    }),
    api.GET("/api/v1/tasks/labels"),
    // The tenant's configured status vocabulary (issue #62): board sections, the pill on each
    // row and the status picker all read from this one list.
    api.GET("/api/v1/tasks/statuses"),
    api.GET("/api/v1/members/lookup"),
    canCreate ? api.GET("/api/v1/tasks/intake/summary") : Promise.resolve({ data: null }),
  ]);
  return {
    intakeWaiting: intake.data?.needs_client ?? 0,
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    labels: labels.data ?? [],
    statuses: statuses.data ?? [],
    members: members.data ?? [],
  };
};
