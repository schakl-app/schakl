import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [contacts, definitions, companies] = await Promise.all([
    api.GET("/api/v1/contacts", { params: { query: { limit: 100, offset: 0 } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
  ]);
  return {
    contacts: contacts.data?.items ?? [],
    total: contacts.data?.total ?? 0,
    definitions: definitions.data ?? [],
    companies: companies.data?.items ?? [],
    locale: event.locals.locale,
  };
};

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });

    const company_id = String(form.get("company_id") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        company_id: company_id || null,
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
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: id } },
      });
    }
    return { deleted: true };
  },
};
