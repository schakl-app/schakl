import { lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Everything this section needs that the URL does not change (#290): the domain picker a website
 * hangs off, the hosting picker, the client/provider/employee/contact pickers, and five sets of
 * custom-field definitions (its own plus the four inline quick-creates).
 *
 * Only the website list itself varies — by filter, sort and page — and it is the one thing those
 * clicks should refetch. A layout load does not rerun on them, so sorting the table costs one
 * round-trip rather than twelve (docs/PERFORMANCE.md).
 *
 * It used to cost twelve *here*, on every entry to the section and on every detail navigation
 * inside it. Three of those are gone and the rest are slim:
 *
 * - **The domain picker is one question, so it is one call.** It used to be answered by
 *   subtraction — every domain (200 rows, fully resolved) minus every website (200 rows, fully
 *   resolved) — which was both the section's two most expensive reads and *wrong past 200
 *   websites*: a domain whose website fell outside that page came back offered as free and 409'd
 *   on save. `GET /websites/available-domains` is a single `NOT EXISTS`, and it is skipped
 *   entirely for a member who cannot create a website.
 * - **Five definition calls are one.** Each re-read the tenant's whole definition set to filter
 *   it in Python, so this section spent five round-trips and five full reads on data that
 *   arrives in one.
 * - **Nothing here draws a total or a resolved display name**, so every picker read passes
 *   `count=false`, and the hosting one `meta=false` — it needs `{id, name}`, not the client name,
 *   provider name and contact label the list screen draws.
 *
 * The website definitions are needed to draw the page, not just a modal: the table renders
 * custom-field *columns* from them.
 *
 * No `await event.parent()` — nothing here depends on the app layout.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  // The create form is the only thing that consumes the free-domain list, and only a writer can
  // submit it. `GET /websites/available-domains` declares the write permission for that reason,
  // so asking as a reader would be a guaranteed 403 (docs/UX.md: never draw a control that 403s).
  const canWrite = can(event.locals.user, "websites.website.write");
  const [availableDomains, hosting, companies, providers, members, contacts, definitions] =
    await Promise.all([
      canWrite
        ? api.GET("/api/v1/websites/available-domains", { params: { query: { limit: 200 } } })
        : Promise.resolve({ data: [] }),
      api.GET("/api/v1/hosting", {
        params: { query: { limit: 200, offset: 0, count: false, meta: false } },
      }),
      api.GET("/api/v1/companies", {
        params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
      }),
      api.GET("/api/v1/providers"),
      api.GET("/api/v1/members/lookup"),
      api.GET("/api/v1/contacts", {
        params: { query: { limit: 200, offset: 0, count: false, sort: "first_name" } },
      }),
      api.GET("/api/v1/custom-fields/definitions/batch", {
        params: { query: { entity_type: ["website", "hosting", "domain", "company", "contact"] } },
      }),
    ]);
  const defs = definitions.data ?? {};
  return {
    // Already filtered to the domains that may still be given a website — the picker does not
    // subtract anything client-side any more.
    availableDomains: availableDomains.data ?? [],
    hosting: (hosting.data?.items ?? []).map((h) => ({ id: h.id, name: h.name })),
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
    definitions: defs.website ?? [],
    hostingDefinitions: defs.hosting ?? [],
    domainDefinitions: defs.domain ?? [],
    companyDefinitions: defs.company ?? [],
    contactDefinitions: defs.contact ?? [],
  };
};
