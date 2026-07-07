import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

const SELECT_TYPES = new Set(["select", "multi_select"]);

export const load: PageServerLoad = async (event) => {
  // Manager-only screen (the API also enforces this on writes).
  if (!event.locals.user?.canManage) throw redirect(303, "/");

  const api = apiFor(event);
  const entityTypesRes = await api.GET("/api/v1/custom-fields/entity-types");
  const entityTypes = entityTypesRes.data ?? [];
  const entity_type = event.url.searchParams.get("entity_type") || entityTypes[0] || "company";

  const definitions = await api.GET("/api/v1/custom-fields/definitions", {
    params: { query: { entity_type, include_inactive: true } },
  });

  return {
    entityTypes,
    entityType: entity_type,
    definitions: definitions.data ?? [],
    locale: event.locals.locale,
  };
};

function parseOptions(raw: FormDataEntryValue | null) {
  return String(raw ?? "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const [value, label] = line.split("|").map((s) => s.trim());
      return { value, label_i18n: { nl: label || value, en: label || value } };
    });
}

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "").trim();
    const key = String(form.get("key") ?? "").trim();
    const data_type = String(form.get("data_type") ?? "text").trim();
    const label_nl = String(form.get("label_nl") ?? "").trim();
    const label_en = String(form.get("label_en") ?? "").trim();
    if (!key || !entity_type) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/custom-fields/definitions", {
      body: {
        entity_type,
        key,
        data_type: data_type as "text",
        label_i18n: { nl: label_nl || key, en: label_en || label_nl || key },
        required: form.get("required") === "on",
        options_json: SELECT_TYPES.has(data_type) ? parseOptions(form.get("options")) : [],
        config_json: {},
        position: Number(form.get("position") ?? 0) || 0,
        active: true,
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { created: true };
  },

  toggleActive: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const active = form.get("active") === "true";
    if (id) {
      await apiFor(event).PATCH("/api/v1/custom-fields/definitions/{definition_id}", {
        params: { path: { definition_id: id } },
        body: { active: !active },
      });
    }
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/custom-fields/definitions/{definition_id}", {
        params: { path: { definition_id: id } },
      });
    }
    return { deleted: true };
  },
};
