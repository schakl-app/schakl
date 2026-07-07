import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const contact_id = event.params.id;
  const [contact, definitions, companies] = await Promise.all([
    api.GET("/api/v1/contacts/{contact_id}", { params: { path: { contact_id } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
  ]);
  if (!contact.data) throw error(404, { code: "not_found", message: "errors.not_found" });
  return {
    contact: contact.data,
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
  update: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });

    const company_id = String(form.get("company_id") ?? "").trim();
    const { error: err } = await apiFor(event).PATCH("/api/v1/contacts/{contact_id}", {
      params: { path: { contact_id: event.params.id } },
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
    if (err) {
      const e = apiErrorKey(err);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/contacts/{contact_id}", {
      params: { path: { contact_id: event.params.id } },
    });
    throw redirect(303, "/contacts");
  },
};
