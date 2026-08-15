import { can } from "$lib/core/permissions";
import { lookupItems } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Eight of the nine calls the domains list used to make are URL-independent (#290), and the
 * detail page repeated most of them again: the client, provider, employee and contact pickers,
 * three sets of custom-field definitions (its own plus the two inline quick-creates), and the
 * TLD price hint. None of them changes when you search, sort, or open a domain.
 *
 * A layout load does not rerun on that navigation, so the list keeps exactly one call and the
 * detail page's thirteen collapse to what is actually about *that* domain
 * (docs/PERFORMANCE.md).
 *
 * The section's own cost is six calls rather than eight: the three definition sets arrive
 * together, because asking per entity type re-read the tenant's whole definition set each time.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  // The TLD price hint (#250) is only fetched for a holder of the read permission.
  const canReadPrices = can(event.locals.user, "domains.tld_price.read");
  const [companies, providers, members, contacts, definitions, prices] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "first_name" } },
    }),
    // The section's own definitions plus the two inline quick-creates', in one call: asking per
    // entity type cost three round-trips and three full reads of the same set, which the service
    // filters in Python either way (docs/PERFORMANCE.md).
    api.GET("/api/v1/custom-fields/definitions/batch", {
      params: { query: { entity_type: ["domain", "company", "contact"] } },
    }),
    canReadPrices ? api.GET("/api/v1/domains/tld-prices") : Promise.resolve({ data: null }),
  ]);
  const defs = definitions.data ?? {};
  return {
    companies: lookupItems(companies, "companies").map((c) => ({
      id: c.id,
      name: c.name,
      status: c.status,
    })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: lookupItems(contacts, "contacts").map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: defs.domain ?? [],
    companyDefinitions: defs.company ?? [],
    contactDefinitions: defs.contact ?? [],
    tldPrices: (prices.data ?? [])
      .filter((g) => g.current != null)
      .map((g) => ({ tld: g.tld, amount: g.current!.amount, currency: g.currency })),
  };
};
