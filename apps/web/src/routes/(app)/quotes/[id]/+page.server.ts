import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createContactAction } from "$lib/core/quickcreate.server";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import "$lib/modules";
import { contactLookups, documentBody, processBody } from "$lib/modules/invoicing/form.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.quote.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const quote_id = event.params.id;
  const context = { entityId: quote_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "quote");

  const [
    quote,
    contacts,
    taxRates,
    products,
    templates,
    settings,
    contactDefinitions,
    ...panelData
  ] = await Promise.all([
    api.GET("/api/v1/invoicing/quotes/{quote_id}", { params: { path: { quote_id } } }),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, count: false, sort: "first_name" } } }),
    api.GET("/api/v1/invoicing/tax-rates"),
    api.GET("/api/v1/invoicing/products"),
    api.GET("/api/v1/invoicing/templates", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/invoicing/settings"),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
    ...panels.map((panel) => panel.load(api, context)),
  ]);
  if (!quote.data) throw httpError(404);

  return {
    quote: quote.data,
    contacts: contactLookups(contacts.data?.items),
    taxRates: taxRates.data ?? [],
    products: products.data ?? [],
    templates: templates.data ?? [],
    settings: settings.data ?? null,
    contactDefinitions: contactDefinitions.data ?? [],
    context,
    panels: panels.map((panel, i) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      data: panelData[i],
    })),
    canWrite: can(event.locals.user, "invoicing.quote.write"),
    canSend: can(event.locals.user, "invoicing.quote.send"),
    canDelete: can(event.locals.user, "invoicing.quote.delete"),
    canInvoice: can(event.locals.user, "invoicing.invoice.write"),
    locale: event.locals.locale,
  };
};

function pathFor(event: { params: { id: string } }) {
  return { params: { path: { quote_id: event.params.id } } };
}

export const actions: Actions = {
  createContact: createContactAction,
  save: async (event) => {
    const form = await event.request.formData();
    const draft = String(form.get("_status") ?? "") === "draft";
    const body: Record<string, unknown> = draft ? documentBody(form) : processBody(form);
    if (form.has("valid_until")) {
      body.valid_until = String(form.get("valid_until") ?? "").trim() || null;
    }
    if (!form.has("lines")) delete body.lines;
    const { error } = await apiFor(event).PATCH("/api/v1/invoicing/quotes/{quote_id}", {
      ...pathFor(event),
      body: body as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
  issue: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/invoicing/quotes/{quote_id}/issue", {
      ...pathFor(event),
      body: {},
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { issued: true };
  },
  send: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/invoicing/quotes/{quote_id}/send", {
      ...pathFor(event),
      body: {
        to: String(form.get("to") ?? "").trim() || null,
        message: String(form.get("message") ?? "").trim() || null,
        email: form.get("email") !== "0",
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sent: true };
  },
  accept: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/invoicing/quotes/{quote_id}/accept", {
      ...pathFor(event),
      body: { note: String(form.get("note") ?? "").trim() || null } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { decided: true };
  },
  reject: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/invoicing/quotes/{quote_id}/reject", {
      ...pathFor(event),
      body: { note: String(form.get("note") ?? "").trim() || null } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { decided: true };
  },
  convert: async (event) => {
    const { data, error } = await apiFor(event).POST(
      "/api/v1/invoicing/quotes/{quote_id}/convert",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, `/invoices/${data.id}`);
  },
  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/quotes/{quote_id}", {
      params: { path: { quote_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, "/quotes");
  },
};
