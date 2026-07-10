import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Roles and the catalog come from `settings/+layout.server.ts`: both screens under Instellingen
// need them, and a layout load does not rerun on tab navigation (docs/PERFORMANCE.md).
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.roles.manage")) throw redirect(303, "/");
  return {};
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const key = String(form.get("key") ?? "").trim();
    if (!key) return fail(400, { error: "errors.required" });
    const from = String(form.get("from") ?? "").trim();

    const { data, error } = await apiFor(event).POST("/api/v1/roles", {
      params: { query: from ? { from } : {} },
      body: {
        key,
        name_i18n: {
          nl: String(form.get("name_nl") ?? "").trim() || key,
          en: String(form.get("name_en") ?? "").trim() || key,
        },
        description_i18n: {},
        position: 100,
        // Omitted when duplicating, so the API copies the source role's set.
        permissions: from ? null : [],
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, `/settings/roles/${data!.id}`);
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("role_id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/roles/{role_id}", {
      params: { path: { role_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
