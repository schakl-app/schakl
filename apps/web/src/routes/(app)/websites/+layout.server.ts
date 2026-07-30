import { lookupItems } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Eleven of the twelve calls this section made are URL-independent (#290): the domain picker a
 * website hangs off, the hosting picker, the client/provider/employee/contact pickers, and five
 * sets of custom-field definitions (its own plus the four inline quick-creates).
 *
 * Only the website list itself varies — by the saved sort — and it is the one thing a sort click
 * should refetch. A layout load does not rerun on that, so sorting the table went from twelve
 * round-trips to one (docs/PERFORMANCE.md).
 *
 * The website definitions are here rather than streamed because the table renders custom-field
 * *columns* from them: they are needed to draw the page, not just a modal.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [
    domains,
    hosting,
    companies,
    providers,
    members,
    contacts,
    definitions,
    hostingDefinitions,
    domainDefinitions,
    companyDefinitions,
    contactDefinitions,
  ] = await Promise.all([
    // A website is a 0/1 child of a domain, so the create picker's options are the tenant's
    // domains — ones that already carry a website are filtered out client-side.
    api.GET("/api/v1/domains", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/hosting", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "first_name" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "website" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "hosting" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "domain" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "contact" } } }),
  ]);
  return {
    domains: (domains.data?.items ?? []).map((d) => ({
      id: d.id,
      name: d.name,
      company_id: d.company_id ?? null,
    })),
    hosting: (hosting.data?.items ?? []).map((h) => ({ id: h.id, name: h.name })),
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: lookupItems(contacts, "contacts").map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: definitions.data ?? [],
    hostingDefinitions: hostingDefinitions.data ?? [],
    domainDefinitions: domainDefinitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
  };
};
