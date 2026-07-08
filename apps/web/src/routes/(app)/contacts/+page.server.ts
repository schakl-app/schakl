import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams.get("q") || undefined;
  const [contacts, definitions] = await Promise.all([
    api.GET("/api/v1/contacts", { params: { query: { limit: 100, offset: 0, q } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ]);
  return {
    contacts: contacts.data?.items ?? [],
    total: contacts.data?.total ?? 0,
    definitions: definitions.data ?? [],
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

    const { error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
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
