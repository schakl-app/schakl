import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "interactions.kind.manage")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/interactions/kinds", {
    params: { query: { include_inactive: true } },
  });
  return { kinds: data ?? [], locale: event.locals.locale };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const kind_id = String(form.get("id") ?? "");
    const label_i18n = {
      nl: String(form.get("label_nl") ?? "").trim(),
      en: String(form.get("label_en") ?? "").trim(),
    };
    const position = Number(form.get("position") ?? 0) || 0;
    if (!label_i18n.nl && !label_i18n.en) return fail(400, { error: "errors.required" });

    if (kind_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/interactions/kinds/{kind_id}", {
        params: { path: { kind_id } },
        body: { label_i18n, position },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      // The tenant only types the label; the immutable key is derived from it (#234).
      const key = slugify(label_i18n.nl || label_i18n.en);
      if (!key) return fail(400, { error: "errors.label_no_key" });
      const { error, response } = await apiFor(event).POST("/api/v1/interactions/kinds", {
        body: { key, label_i18n, position, active: true },
      });
      if (error) return fail(400, { error: createErrorKey(error, response) });
    }
    return { saved: true };
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const kind_id = String(form.get("id") ?? "");
    if (kind_id) {
      await apiFor(event).PATCH("/api/v1/interactions/kinds/{kind_id}", {
        params: { path: { kind_id } },
        body: { active: form.get("active") === "true" },
      });
    }
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const kind_id = String(form.get("id") ?? "");
    if (kind_id) {
      const { error } = await apiFor(event).DELETE("/api/v1/interactions/kinds/{kind_id}", {
        params: { path: { kind_id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { deleted: true };
  },
};
