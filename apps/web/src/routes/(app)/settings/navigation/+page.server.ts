import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { buildNavDefaultPayload } from "$lib/core/navpref";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.nav.manage")) throw redirect(303, "/");
  const { data: prefs } = await apiFor(event).GET("/api/v1/nav/prefs");
  const isDefault = prefs?.source === "default";
  return {
    // The default applies to everyone without a personal layout; like Settings → Dashboard,
    // the admin's own personal row (source "user") does not stand in for it here.
    defaultItems: isDefault ? (prefs.items ?? null) : null,
    // The org's saved group labels (#169), edited alongside the item order/labels.
    defaultGroups: isDefault ? (prefs.groups ?? []) : [],
  };
};

export const actions: Actions = {
  saveDefault: async (event) => {
    const form = await event.request.formData();
    const { items, groups } = buildNavDefaultPayload(form);
    const { error } = await apiFor(event).PUT("/api/v1/nav/prefs/default", {
      body: { items, groups },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
