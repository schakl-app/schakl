import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The four URL-independent lookups every screen under `contacts/` needs (#290): the tenant's
 * contact custom fields, the company ones (the picker's inline company create opens the full
 * dialog, real fields and all), the client picker itself, and the contact-type vocabulary.
 *
 * A layout load does not rerun on filter, sort or detail navigation, so these happen once per
 * visit to the section — the list page and the detail page each fetched their own copies of
 * three of them (docs/PERFORMANCE.md).
 *
 * No `await event.parent()`: this fan depends on nothing the app layout produces, and awaiting
 * first would serialise it behind that load.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [definitions, companyDefinitions, companies, types] = await Promise.all([
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "contact" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/contacts/types"),
  ]);
  return {
    definitions: definitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    companies: companies.data?.items ?? [],
    types: types.data ?? [],
  };
};
