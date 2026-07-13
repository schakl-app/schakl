import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { parseNavItems } from "$lib/core/navpref";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.nav.manage")) throw redirect(303, "/");
  const { data: prefs } = await apiFor(event).GET("/api/v1/nav/prefs");
  return {
    // The default applies to everyone without a personal layout; like Settings → Dashboard,
    // the admin's own personal row (source "user") does not stand in for it here.
    defaultItems: prefs?.source === "default" ? (prefs.items ?? null) : null,
  };
};

export const actions: Actions = {
  saveDefault: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/nav/prefs/default", {
      body: { items: parseNavItems(form.get("items")) },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
