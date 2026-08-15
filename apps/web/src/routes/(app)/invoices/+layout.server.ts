import { lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The client picker and the header tiles, shared by every screen under `invoices/` and varying
 * with none of their URLs (#290). A layout load does not rerun on a filter, sort or detail
 * click, so they cost one call each per visit to the section.
 *
 * The tiles are org-wide totals, not a view of the current filter, so they are *correct* to
 * survive a filter change — and a create or delete goes through an enhanced form, which
 * invalidates and reruns layout loads too, so they still stay fresh.
 *
 * The tiles are horizon-scoped by the API, so a client portal login (#266) sees *their* open
 * and overdue totals — which is the one figure a client page is for. The company **picker**
 * is skipped for them: a filter over the single company they can see is a control that can
 * only ever be a no-op, and skipping it is one fewer call on their page
 * (docs/PERFORMANCE.md).
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const crossCompany = can(event.locals.user, "invoicing.invoice.read", "any");
  const [summary, companies] = await Promise.all([
    api.GET("/api/v1/invoicing/summary"),
    crossCompany
      ? api.GET("/api/v1/companies", {
          params: { query: { limit: 200, count: false, sort: "name" } },
        })
      : undefined,
  ]);
  return {
    summary: summary.data ?? null,
    companies: companies
      ? lookupItems(companies, "companies").map((c) => ({
          id: c.id,
          name: c.name,
          status: c.status,
        }))
      : [],
  };
};
