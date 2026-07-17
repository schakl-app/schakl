import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Service access (epic #199, cloud only): the org's own switch on platform-support access.
// Generating a PIN is the tenant's consent; revoking it slams the door again.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.service_access.manage")) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/settings/service-access");
  // 404 = not a cloud deployment; the page simply doesn't exist here.
  if (!data) throw redirect(303, "/settings");
  return { access: data };
};

export const actions: Actions = {
  generate: async (event) => {
    const { data, error } = await apiFor(event).POST("/api/v1/settings/service-access");
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    return { pin: data.pin, expiresAt: data.expires_at };
  },

  revoke: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/settings/service-access");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { revoked: true };
  },
};
