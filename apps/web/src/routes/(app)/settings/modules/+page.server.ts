import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const api = apiFor(event);
  const [instance, tenant] = await Promise.all([
    api.GET("/api/v1/meta/modules"),
    api.GET("/api/v1/meta/tenant"),
  ]);
  return {
    // What this installation ships vs. what this workspace has switched on.
    available: instance.data?.enabled_modules ?? [],
    enabled: tenant.data?.enabled_modules ?? [],
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const enabled_modules = form.getAll("modules").map(String).filter(Boolean);
    const { error } = await apiFor(event).PATCH("/api/v1/meta/tenant", {
      body: { enabled_modules },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },
};
