import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// License management is instance-owner gated (issue #137) — users.is_superuser, not an
// org permission: the license belongs to the installation, not to a tenant.
export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.isInstanceOwner) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/instance/license");
  return { license: data ?? null };
};

export const actions: Actions = {
  install: async (event) => {
    const form = await event.request.formData();
    const key = String(form.get("key") ?? "").trim();
    if (!key) return fail(400, { error: "errors.license_invalid" });
    const { data, error } = await apiFor(event).PUT("/api/v1/instance/license", {
      body: { key },
    });
    if (error) return fail(422, { error: apiErrorKey(error).key });
    return { installed: true, license: data };
  },
};
