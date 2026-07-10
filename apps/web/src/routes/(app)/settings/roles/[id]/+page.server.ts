import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.roles.manage")) throw redirect(303, "/");
  // Roles and the catalog were already fetched by `settings/+layout.server.ts`; this page adds
  // no request of its own (docs/PERFORMANCE.md).
  const { roles } = await event.parent();
  const role = roles.find((r) => r.id === event.params.id);
  if (!role) throw error(404, "errors.not_found");
  return { role };
};

/**
 * Decode the matrix into the flat permission list the API stores.
 *
 * Unscoped permissions arrive as repeated `permissions` values. Scoped ones arrive as
 * `scope:<key>` with `""` / `own` / `any`, because a checkbox cannot express *whose* records a
 * grant covers, and that distinction is the whole point of a scope qualifier.
 */
function readPermissions(form: FormData): string[] {
  const permissions = form.getAll("permissions").map(String).filter(Boolean);
  for (const [field, raw] of form.entries()) {
    if (!field.startsWith("scope:")) continue;
    const scope = String(raw);
    if (scope === "own" || scope === "any") permissions.push(`${field.slice(6)}:${scope}`);
  }
  return [...new Set(permissions)];
}

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const isOwner = String(form.get("is_owner") ?? "") === "true";

    const { error: apiError } = await apiFor(event).PATCH("/api/v1/roles/{role_id}", {
      params: { path: { role_id: event.params.id } },
      body: {
        name_i18n: {
          nl: String(form.get("name_nl") ?? "").trim(),
          en: String(form.get("name_en") ?? "").trim(),
        },
        description_i18n: {
          nl: String(form.get("description_nl") ?? "").trim(),
          en: String(form.get("description_en") ?? "").trim(),
        },
        // The owner always holds `*`; sending a permission set for it is a 409 by design.
        permissions: isOwner ? null : readPermissions(form),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { saved: true };
  },
};
