import { error as httpError, redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

/**
 * Just enough to title the page. The document itself is fetched by `DocumentFrame` from
 * `/preview`, which renders it server-side — so the template list and the seller settings
 * this load used to pull are two round trips nothing on the page reads (docs/PERFORMANCE.md).
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.quote.read")) throw redirect(303, "/");
  const quote = await apiFor(event).GET("/api/v1/invoicing/quotes/{quote_id}", {
    params: { path: { quote_id: event.params.id } },
  });
  if (!quote.data) throw httpError(404);
  return { quote: quote.data };
};
