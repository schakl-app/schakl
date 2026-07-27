import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { parseParty } from "$lib/core/party";
import { can } from "$lib/core/permissions";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  // A settings screen guards itself (#19); the API enforces the permission too.
  if (!can(event.locals.user, "hosting.hosting.read")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [
    hosting,
    companies,
    providers,
    members,
    contacts,
    definitions,
    companyDefinitions,
    contactDefinitions,
  ] = await Promise.all([
    api.GET("/api/v1/hosting", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, offset: 0, sort: "first_name" } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "hosting" } },
    }),
    // For the inline quick-creates (#115): their full dialogs include custom fields.
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ]);

  return {
    hosting: hosting.data?.items ?? [],
    total: hosting.data?.total ?? 0,
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: lookupItems(contacts, "contacts").map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: definitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
    agencyLabel: event.locals.theme?.brandName ?? "",
    locale: event.locals.locale,
  };
};

function hostingBody(form: FormData) {
  return {
    name: String(form.get("name") ?? "").trim(),
    company_id: String(form.get("company_id") ?? "") || null,
    provider_id: String(form.get("provider_id") ?? "") || null,
    ip_address: String(form.get("ip_address") ?? "").trim() || null,
    contact: parseParty(form.get("contact")),
    custom: parseCustom(form.get("custom")),
  };
}

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const hosting_id = String(form.get("id") ?? "");
    const body = hostingBody(form);
    if (!body.name) return fail(400, { error: "errors.required" });

    if (hosting_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/hosting/{hosting_id}", {
        params: { path: { hosting_id } },
        body,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const { error } = await apiFor(event).POST("/api/v1/hosting", { body });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const hosting_id = String(form.get("id") ?? "");
    if (hosting_id) {
      await apiFor(event).DELETE("/api/v1/hosting/{hosting_id}", {
        params: { path: { hosting_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,
};
