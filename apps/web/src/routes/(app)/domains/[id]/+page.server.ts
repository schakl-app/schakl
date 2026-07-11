import { error, fail, redirect } from "@sveltejs/kit";

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
  const domain_id = event.params.id;

  const [domain, companies, providers, members, contacts, defs, websiteDefs, websites, hosting] =
    await Promise.all([
      api.GET("/api/v1/domains/{domain_id}", { params: { path: { domain_id } } }),
      api.GET("/api/v1/companies", { params: { query: { limit: 500, offset: 0, count: false } } }),
      api.GET("/api/v1/providers"),
      api.GET("/api/v1/members/lookup"),
      api.GET("/api/v1/contacts", { params: { query: { limit: 500, offset: 0 } } }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "domain" } },
      }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "website" } },
      }),
      api.GET("/api/v1/websites", { params: { query: { domain_id, limit: 1, offset: 0 } } }),
      api.GET("/api/v1/hosting", { params: { query: { limit: 500, offset: 0 } } }),
    ]);

  if (!domain.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    domain: domain.data,
    companies: (companies.data?.items ?? []).map((c) => ({ id: c.id, name: c.name })),
    providers: providers.data ?? [],
    employees: members.data ?? [],
    contacts: (contacts.data?.items ?? []).map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    })),
    definitions: defs.data ?? [],
    websiteDefinitions: websiteDefs.data ?? [],
    website: websites.data?.items?.[0] ?? null,
    hosting: (hosting.data?.items ?? []).map((h) => ({ id: h.id, name: h.name })),
    agencyLabel: event.locals.theme?.brandName ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const email_enabled = form.get("email_enabled") === "on";
    const { error: err } = await apiFor(event).PATCH("/api/v1/domains/{domain_id}", {
      params: { path: { domain_id: event.params.id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        company_id: String(form.get("company_id") ?? "") || undefined,
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
    if (err) {
      const e = apiErrorKey(err);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  refresh: async (event) => {
    const { error: err } = await apiFor(event).POST("/api/v1/domains/{domain_id}/refresh", {
      params: { path: { domain_id: event.params.id } },
    });
    if (err) return fail(400, { error: apiErrorKey(err).key });
    return { refreshed: true };
  },

  saveWebsite: async (event) => {
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
      const { error: err } = await apiFor(event).PATCH("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
        body,
      });
      if (err) return fail(400, { error: apiErrorKey(err).key });
    } else {
      const { error: err } = await apiFor(event).POST("/api/v1/websites", {
        body: { ...body, domain_id: event.params.id },
      });
      if (err) return fail(400, { error: apiErrorKey(err).key });
    }
    return { websiteSaved: true };
  },

  deleteWebsite: async (event) => {
    const form = await event.request.formData();
    const website_id = String(form.get("website_id") ?? "");
    if (website_id) {
      await apiFor(event).DELETE("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
      });
    }
    return { websiteDeleted: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/domains/{domain_id}", {
      params: { path: { domain_id: event.params.id } },
    });
    throw redirect(303, "/domains");
  },
};
