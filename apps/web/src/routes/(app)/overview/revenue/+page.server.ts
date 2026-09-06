import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readYear } from "$lib/core/today";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const user = event.locals.user;
  if (!can(user, "time.report.read")) throw redirect(303, "/overview");
  const api = apiFor(event);
  const year = readYear(event.url);
  // The one toggle on the page: which of a document's two totals the figures print. The URL
  // carries it, so a link to "incl. btw" stays one (docs/UX.md, "the URL is the view").
  const vat = event.url.searchParams.get("vat") === "incl" ? "incl" : "excl";
  const enabled = event.locals.theme?.enabledModules ?? [];
  const invoicing = enabled.includes("invoicing") && can(user, "invoicing.invoice.read", "any");

  const [invoiced, hoursValue, companies] = await Promise.all([
    invoicing
      ? api
          .GET("/api/v1/invoicing/stats/revenue", { params: { query: { year } } })
          .then((r) => r.data ?? null)
      : Promise.resolve(null),
    api
      .GET("/api/v1/time/stats/revenue", { params: { query: { year } } })
      .then((r) => r.data ?? null),
    // Names for the hours-value ranking, which the time module reports by client id.
    api
      .GET("/api/v1/companies", {
        params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
      })
      .then((r) => r.data?.items ?? []),
  ]);
  return { year, vat, invoicing, invoiced, hoursValue, companies };
};
