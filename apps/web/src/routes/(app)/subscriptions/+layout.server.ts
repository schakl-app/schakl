import { lookupItems } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The URL-independent pickers under `subscriptions/` (#290): the client and project pickers the
 * create/edit forms use, the tenant's subscription custom fields, and the company ones the
 * inline client quick-create needs.
 *
 * Deliberately **not** the type vocabulary or the templates, even though the list page fetches
 * both: the `types/` and `templates/` tabs return their own filtered, sorted versions under the
 * same keys, and a page's data wins over its layout's. Hoisting them would leave every visit to
 * those tabs paying for a copy it immediately discards.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [companies, projects, definitions, companyDefinitions] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "subscription" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
  ]);
  return {
    companies: lookupItems(companies, "companies").map((c) => ({
      id: c.id,
      name: c.name,
      status: c.status,
    })),
    // With the client, so the agreement form's links picker can narrow to the agreement's.
    projects: lookupItems(projects, "projects").map((p) => ({
      id: p.id,
      name: p.name,
      status: p.status,
      company_id: p.company_id,
    })),
    definitions: definitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
  };
};
