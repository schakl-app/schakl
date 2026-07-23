import { fail } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { parseParty } from "$lib/core/party";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { DOMAIN_COLUMNS, DOMAINS_TABLE_ID } from "$lib/modules/domains/columns";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams.get("q") || undefined;

  // The saved column layout comes from the layout load (docs/PERFORMANCE.md). The URL wins
  // over the saved sort so a sorted list stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, DOMAINS_TABLE_ID);
  const resolved = resolveColumns(DOMAIN_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  const [
    domains,
    companies,
    providers,
    members,
    contacts,
    definitions,
    companyDefinitions,
    contactDefinitions,
  ] = await Promise.all([
    api.GET("/api/v1/domains", { params: { query: { limit: 200, offset: 0, q, sort } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, offset: 0, sort: "first_name" } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "domain" } },
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
    domains: domains.data?.items ?? [],
    total: domains.data?.total ?? 0,
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
    q: q ?? "",
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, DOMAINS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const company_id = String(form.get("company_id") ?? "");
    if (!name || !company_id) return fail(400, { error: "errors.required" });
    const email_enabled = form.get("email_enabled") !== null;

    const { error } = await apiFor(event).POST("/api/v1/domains", {
      body: {
        name,
        company_id,
        status: String(form.get("status") ?? "active") as never,
        redirect_url: String(form.get("redirect_url") ?? "").trim() || null,
        registrar_provider_id: String(form.get("registrar_provider_id") ?? "") || null,
        dns_provider_id: String(form.get("dns_provider_id") ?? "") || null,
        registry_contact: parseParty(form.get("registry_contact")),
        email_enabled,
        email_provider_id: email_enabled
          ? String(form.get("email_provider_id") ?? "") || null
          : null,
        email_contact: email_enabled ? parseParty(form.get("email_contact")) : null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { created: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const domain_id = String(form.get("id") ?? "");
    if (domain_id) {
      await apiFor(event).DELETE("/api/v1/domains/{domain_id}", {
        params: { path: { domain_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,
};
