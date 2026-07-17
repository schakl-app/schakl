import { error as httpError, redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.quote.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [quote, templates, settings] = await Promise.all([
    api.GET("/api/v1/invoicing/quotes/{quote_id}", {
      params: { path: { quote_id: event.params.id } },
    }),
    api.GET("/api/v1/invoicing/templates", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/invoicing/settings"),
  ]);
  if (!quote.data) throw httpError(404);
  return {
    quote: quote.data,
    templates: templates.data ?? [],
    settings: settings.data ?? null,
  };
};
