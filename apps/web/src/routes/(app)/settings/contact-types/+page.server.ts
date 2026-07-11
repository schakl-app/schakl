import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "contacts.type.manage")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/contacts/types", {
    params: { query: { include_inactive: true } },
  });
  return { types: data ?? [], locale: event.locals.locale };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    const label_i18n = {
      nl: String(form.get("label_nl") ?? "").trim(),
      en: String(form.get("label_en") ?? "").trim(),
    };
    const position = Number(form.get("position") ?? 0) || 0;
    if (!label_i18n.nl || !label_i18n.en) return fail(400, { error: "errors.required" });

    if (type_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/contacts/types/{type_id}", {
        params: { path: { type_id } },
        body: { label_i18n, position },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const key = String(form.get("key") ?? "").trim();
      if (!key) return fail(400, { error: "errors.required" });
      const { error } = await apiFor(event).POST("/api/v1/contacts/types", {
        body: { key, label_i18n, position, active: true },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    if (type_id) {
      await apiFor(event).PATCH("/api/v1/contacts/types/{type_id}", {
        params: { path: { type_id } },
        body: { active: form.get("active") === "true" },
      });
    }
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const type_id = String(form.get("id") ?? "");
    if (type_id) {
      await apiFor(event).DELETE("/api/v1/contacts/types/{type_id}", {
        params: { path: { type_id } },
      });
    }
    return { deleted: true };
  },
};
