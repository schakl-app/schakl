import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The URL-independent lookups under `meetings/`: the clients and projects the recorder and the
 * review screen pick from, the staff the action items are handed to, and the contact custom
 * fields the participants editor's inline-create dialog draws. A layout load, so a search
 * keystroke or a page step on the list reruns none of them (docs/PERFORMANCE.md).
 *
 * `count: false`, `meta: false`: pickers want names, not totals.
 */
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "meetings.meeting.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [companies, projects, members, contactDefinitions] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, meta: false, sort: "name" } },
    }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ]);
  return {
    companies: (companies.data?.items ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      status: c.status,
    })),
    projects: (projects.data?.items ?? []).map((p) => ({
      id: p.id,
      name: p.name,
      status: p.status,
      company_id: p.company_id,
    })),
    members: members.data ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
  };
};
