import "$lib/modules"; // ensure widgets are registered before we read the registry

import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { dashboardWidgetsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// My Day composes widgets contributed by the enabled modules — the core page stays generic.
// The user's saved layout (or the org template) decides which widgets show, in which order.
export const load: PageServerLoad = async (event) => {
  const enabled = event.locals.theme?.enabledModules ?? [];
  const canManage = event.locals.user?.canManage ?? false;
  const available = dashboardWidgetsFor(enabled).filter(
    (w) => !w.requiresManage || canManage,
  );
  const api = apiFor(event);

  const { data: prefs } = await api.GET("/api/v1/dashboard/prefs");
  const layout = prefs?.widgets ?? null;
  const widgets = layout
    ? layout
        .map((key) => available.find((w) => w.key === key))
        .filter((w): w is (typeof available)[number] => Boolean(w))
    : available;

  const results = await Promise.all(
    widgets.map(async (w) => ({ key: w.key, data: await w.load(api) })),
  );
  const widgetData: Record<string, unknown> = {};
  for (const r of results) widgetData[r.key] = r.data;

  return {
    widgetKeys: widgets.map((w) => w.key),
    availableWidgetKeys: available.map((w) => w.key),
    prefsSource: prefs?.source ?? "none",
    widgetData,
  };
};

function parseWidgets(form: FormData): string[] {
  return String(form.get("widgets") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export const actions: Actions = {
  saveLayout: async (event) => {
    const widgets = parseWidgets(await event.request.formData());
    const { error } = await apiFor(event).PUT("/api/v1/dashboard/prefs", {
      body: { widgets },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  saveDefaultLayout: async (event) => {
    const widgets = parseWidgets(await event.request.formData());
    const { error } = await apiFor(event).PUT("/api/v1/dashboard/prefs/default", {
      body: { widgets },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  resetLayout: async (event) => {
    await apiFor(event).DELETE("/api/v1/dashboard/prefs");
    return { saved: true };
  },
};
