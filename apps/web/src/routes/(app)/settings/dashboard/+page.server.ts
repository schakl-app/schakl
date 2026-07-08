import "$lib/modules"; // populate the widget registry

import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { dashboardWidgetsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const enabled = event.locals.theme?.enabledModules ?? [];
  const { data: prefs } = await apiFor(event).GET("/api/v1/dashboard/prefs");
  return {
    availableWidgetKeys: dashboardWidgetsFor(enabled).map((w) => w.key),
    // The template applies to everyone without a personal layout; "none" = all widgets.
    defaultWidgets: prefs?.source === "default" ? (prefs.widgets ?? null) : null,
  };
};

export const actions: Actions = {
  saveDefault: async (event) => {
    const form = await event.request.formData();
    const widgets = String(form.get("widgets") ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const { error } = await apiFor(event).PUT("/api/v1/dashboard/prefs/default", {
      body: { widgets },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
