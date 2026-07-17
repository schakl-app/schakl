import { error as httpError, redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.invoice.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [invoice, templates, settings] = await Promise.all([
    api.GET("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id: event.params.id } },
    }),
    api.GET("/api/v1/invoicing/templates", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/invoicing/settings"),
  ]);
  if (!invoice.data) throw httpError(404);
  return {
    invoice: invoice.data,
    templates: templates.data ?? [],
    settings: settings.data ?? null,
  };
};
