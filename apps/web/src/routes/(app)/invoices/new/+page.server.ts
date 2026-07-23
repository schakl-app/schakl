import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction, createContactAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { contactLookups, documentBody } from "$lib/modules/invoicing/form.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.invoice.write")) throw redirect(303, "/invoices");
  const api = apiFor(event);
  const [
    companies,
    contacts,
    taxRates,
    products,
    templates,
    settings,
    companyDefinitions,
    contactDefinitions,
  ] = await Promise.all([
    api.GET("/api/v1/companies", { params: { query: { limit: 200, count: false, sort: "name" } } }),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, count: false, sort: "first_name" } } }),
    api.GET("/api/v1/invoicing/tax-rates"),
    api.GET("/api/v1/invoicing/products"),
    api.GET("/api/v1/invoicing/templates"),
    api.GET("/api/v1/invoicing/settings"),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ]);
  return {
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    contacts: contactLookups(contacts.data?.items),
    taxRates: taxRates.data ?? [],
    products: products.data ?? [],
    templates: templates.data ?? [],
    settings: settings.data ?? null,
    companyDefinitions: companyDefinitions.data ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "");
    if (!company_id) return fail(400, { error: "errors.required" });
    const body = documentBody(form);
    const { data, error } = await apiFor(event).POST("/api/v1/invoicing/invoices", {
      body: {
        ...body,
        company_id,
        due_date: String(form.get("due_date") ?? "").trim() || null,
      } as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    throw redirect(303, `/invoices/${data.id}`);
  },
  createCompany: createCompanyAction,
  createContact: createContactAction,
};
