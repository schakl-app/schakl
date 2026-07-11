import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { parseParty } from "$lib/core/party";
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
  const api = apiFor(event);
  const q = event.url.searchParams.get("q") || undefined;
  const sort = event.url.searchParams.get("sort") ?? undefined;

  const [domains, companies, providers, members, contacts, definitions] = await Promise.all([
    api.GET("/api/v1/domains", { params: { query: { limit: 200, offset: 0, q, sort } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 500, offset: 0, count: false } } }),
    api.GET("/api/v1/providers"),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", { params: { query: { limit: 500, offset: 0 } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "domain" } } }),
  ]);

  return {
    domains: domains.data?.items ?? [],
    total: domains.data?.total ?? 0,
    companies: (companies.data?.items ?? []).map((c) => ({ id: c.id, name: c.name })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: (contacts.data?.items ?? []).map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: definitions.data ?? [],
    agencyLabel: event.locals.theme?.brandName ?? "",
    q: q ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const company_id = String(form.get("company_id") ?? "");
    if (!name || !company_id) return fail(400, { error: "errors.required" });
    const email_enabled = form.get("email_enabled") === "on";

    const { error } = await apiFor(event).POST("/api/v1/domains", {
      body: {
        name,
        company_id,
        status: String(form.get("status") ?? "active") as never,
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
};
