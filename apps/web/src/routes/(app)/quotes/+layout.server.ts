import { lookupItems } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The client picker, shared by every screen under `quotes/` and varying with none of their URLs
 * (#290) — a layout load does not rerun on a filter, sort or detail click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const companies = await api.GET("/api/v1/companies", {
    params: { query: { limit: 200, count: false, sort: "name" } },
  });
  return {
    companies: lookupItems(companies, "companies").map((c) => ({
      id: c.id,
      name: c.name,
      status: c.status,
    })),
  };
};
