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
  // The API enforces the permission too; redirect a member who lacks it (the nav item is
  // already hidden for them).
  if (!can(event.locals.user, "websites.website.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const [
    websites,
    domains,
    hosting,
    companies,
    providers,
    members,
    contacts,
    definitions,
    hostingDefinitions,
    companyDefinitions,
    contactDefinitions,
  ] = await Promise.all([
    api.GET("/api/v1/websites", { params: { query: { limit: 200, offset: 0 } } }),
    // The create picker: a website is a 0/1 child of a domain, so the options are the
    // tenant's domains — ones that already carry a website are filtered out client-side.
    api.GET("/api/v1/domains", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/hosting", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "website" } },
    }),
    // For the inline hosting quick-create (#115): its full dialog includes custom fields.
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "hosting" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ]);

  return {
    websites: websites.data?.items ?? [],
    total: websites.data?.total ?? 0,
    domains: (domains.data?.items ?? []).map((d) => ({
      id: d.id,
      name: d.name,
      company_id: d.company_id ?? null,
    })),
    hosting: (hosting.data?.items ?? []).map((h) => ({ id: h.id, name: h.name })),
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: lookupItems(contacts, "contacts").map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: definitions.data ?? [],
    hostingDefinitions: hostingDefinitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
    agencyLabel: event.locals.theme?.brandName ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const website_id = String(form.get("website_id") ?? "");
    const body = {
      root: form.get("root") !== "www",
      technical_owner: parseParty(form.get("technical_owner")),
      hosting_id: String(form.get("hosting_id") ?? "") || null,
      uptime_enabled: form.get("uptime_enabled") === "on",
      custom: parseCustom(form.get("custom")),
    };
    if (website_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
        body,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const domain_id = String(form.get("domain_id") ?? "");
      if (!domain_id) return fail(400, { error: "errors.required" });
      const { error } = await apiFor(event).POST("/api/v1/websites", {
        body: { ...body, domain_id },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const website_id = String(form.get("id") ?? "");
    if (website_id) {
      await apiFor(event).DELETE("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,

  // Inline-create for the hosting picker (#115): the full HostingForm in a modal.
  createHosting: async (event) => {
    const form = await event.request.formData();
    const body = {
      name: String(form.get("name") ?? "").trim(),
      company_id: String(form.get("company_id") ?? "") || null,
      provider_id: String(form.get("provider_id") ?? "") || null,
      ip_address: String(form.get("ip_address") ?? "").trim() || null,
      contact: parseParty(form.get("contact")),
      custom: parseCustom(form.get("custom")),
    };
    if (!body.name) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/hosting", { body });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return { inlineCreated: { slot: "hosting_account", id: data.id } };
  },
};
