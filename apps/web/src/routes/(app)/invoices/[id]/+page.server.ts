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
  if (!can(event.locals.user, "invoicing.invoice.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const invoice_id = event.params.id;
  const context = { entityId: invoice_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "invoice");

  const [
    invoice,
    contacts,
    taxRates,
    products,
    templates,
    settings,
    contactDefinitions,
    ...panelData
  ] = await Promise.all([
    api.GET("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id } },
    }),
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
  if (!invoice.data) throw httpError(404);

  return {
    invoice: invoice.data,
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
    canWrite: can(event.locals.user, "invoicing.invoice.write"),
    canSend: can(event.locals.user, "invoicing.invoice.send"),
    canDelete: can(event.locals.user, "invoicing.invoice.delete"),
    canPay: can(event.locals.user, "invoicing.payment.write"),
    locale: event.locals.locale,
  };
};

type InvoicePath = { params: { path: { invoice_id: string } } };

function pathFor(event: { params: { id: string } }): InvoicePath {
  return { params: { path: { invoice_id: event.params.id } } };
}

export const actions: Actions = {
  createContact: createContactAction,
  save: async (event) => {
    const form = await event.request.formData();
    const draft = String(form.get("_status") ?? "") === "draft";
    const body: Record<string, unknown> = draft ? documentBody(form) : processBody(form);
    // Only fields the form actually posted may change — the sidebar's reminders toggle,
    // for example, must not clear the due date it never carried.
    if (form.has("due_date")) {
      body.due_date = String(form.get("due_date") ?? "").trim() || null;
    }
    if (form.has("reminders_paused")) {
      body.reminders_paused = form.get("reminders_paused") === "1";
    }
    if (!form.has("lines")) delete body.lines;
    if (!form.has("contact_id")) delete body.contact_id;
    if (!form.has("locale")) delete body.locale;
    if (!form.has("template_id")) delete body.template_id;
    if (!form.has("reference")) delete body.reference;
    if (!form.has("intro")) delete body.intro;
    if (!form.has("notes")) delete body.notes;
    if (!form.has("exchange_rate")) delete body.exchange_rate;
    const { error } = await apiFor(event).PATCH("/api/v1/invoicing/invoices/{invoice_id}", {
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
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/issue", {
      ...pathFor(event),
      body: {},
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { issued: true };
  },
  send: async (event) => {
    const form = await event.request.formData();
    const to = String(form.get("to") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/send", {
      ...pathFor(event),
      body: {
        to: to || null,
        message: String(form.get("message") ?? "").trim() || null,
        email: form.get("email") !== "0",
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sent: true };
  },
  remind: async (event) => {
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/remind",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { reminded: true };
  },
  cancel: async (event) => {
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/cancel",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { cancelled: true };
  },
  credit: async (event) => {
    const { data, error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/credit",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, `/invoices/${data.id}`);
  },
  payment: async (event) => {
    const form = await event.request.formData();
    const paid_on = String(form.get("paid_on") ?? "");
    const amount = String(form.get("amount") ?? "");
    if (!paid_on || !amount) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/payments", {
      ...pathFor(event),
      body: {
        paid_on,
        amount,
        method: String(form.get("method") ?? "bank"),
        note: String(form.get("note") ?? "").trim() || null,
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { paymentSaved: true };
  },
  deletePayment: async (event) => {
    const form = await event.request.formData();
    const payment_id = String(form.get("payment_id") ?? "");
    const { error } = await apiFor(event).DELETE(
      "/api/v1/invoicing/invoices/{invoice_id}/payments/{payment_id}",
      { params: { path: { invoice_id: event.params.id, payment_id } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { paymentDeleted: true };
  },
  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, "/invoices");
  },
};
