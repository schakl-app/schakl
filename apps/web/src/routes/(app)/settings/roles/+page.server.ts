import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";

import type { Actions, PageServerLoad } from "./$types";

// Roles and the catalog come from `settings/+layout.server.ts`: both screens under Instellingen
// need them, and a layout load does not rerun on tab navigation (docs/PERFORMANCE.md).
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.roles.manage")) throw redirect(303, "/");
  return {};
};

export const actions: Actions = {
  // Failures land in `createError`, rendered inside the create modal — the page-level
  // `error` banner sits behind the modal overlay and belongs to the delete flow (#234).
  create: async (event) => {
    const form = await event.request.formData();
    const name_nl = String(form.get("name_nl") ?? "").trim();
    const name_en = String(form.get("name_en") ?? "").trim();
    if (!name_nl && !name_en) return fail(400, { createError: "errors.required" });
    const from = String(form.get("from") ?? "").trim();
    // The tenant only types the name; the immutable key is derived from it (#234). Every
    // slugify() result already fits the API's `^[a-z0-9][a-z0-9_-]{0,62}$`.
    const key = slugify(name_nl || name_en);
    if (!key) return fail(400, { createError: "errors.label_no_key" });

    const { data, error, response } = await apiFor(event).POST("/api/v1/roles", {
      params: { query: from ? { from } : {} },
      body: {
        key,
        name_i18n: { nl: name_nl || name_en, en: name_en || name_nl },
        description_i18n: {},
        position: 100,
        // Omitted when duplicating, so the API copies the source role's set.
        permissions: from ? null : [],
      },
    });
    if (error) return fail(400, { createError: createErrorKey(error, response) });
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
