import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const company_id = event.params.id;

  const { data: company } = await api.GET("/api/v1/companies/{company_id}", {
    params: { path: { company_id } },
  });
  if (!company) throw error(404, { code: "not_found", message: "errors.not_found" });

  const [panels, definitions, templates] = await Promise.all([
    api.GET("/api/v1/companies/{company_id}/panels", { params: { path: { company_id } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/tasks/templates"),
  ]);

  return {
    company,
    panels: panels.data ?? [],
    definitions: definitions.data ?? [],
    templates: templates.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const { error: apiError } = await apiFor(event).PATCH("/api/v1/companies/{company_id}", {
      params: { path: { company_id: event.params.id } },
      body: {
        name,
        website: String(form.get("website") ?? "").trim() || null,
        notes: String(form.get("notes") ?? "").trim() || null,
        status: String(form.get("status") ?? "active") as "active",
        custom: parseCustom(form.get("custom")),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { updated: true };
  },

  addContact: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        company_id: event.params.id,
        custom: {},
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { contactAdded: true };
  },

  applyTemplate: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("template_id") ?? "");
    if (!template_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/templates/{template_id}/apply",
      {
        params: { path: { template_id } },
        body: { company_id: event.params.id },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { templateApplied: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/companies/{company_id}", {
      params: { path: { company_id: event.params.id } },
    });
    throw redirect(303, "/companies");
  },
};
