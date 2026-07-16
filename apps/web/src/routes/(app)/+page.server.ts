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
  const api = apiFor(event);

  // The client portal's homepage (#193): a contact-linked login lands on their companies'
  // curated marketing dashboards (#192 layouts, enforced server-side), not the staff My Day.
  // The companies list is already horizon-scoped by the API, so this can't over-fetch.
  if (event.locals.user?.isPortal) {
    const { data: companies } = await api.GET("/api/v1/companies", {
      params: { query: { limit: 50, count: false } },
    });
    const items = (companies?.items ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      // The client's own logo (#196), served tenant+horizon-scoped by the API.
      logoUrl: c.logo_file_id ? `/api/v1/companies/${c.id}/logo` : null,
    }));
    const selected = event.url.searchParams.get("company") ?? items[0]?.id ?? null;
    const metrics = selected
      ? (
          await api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
            params: { path: { company_id: selected }, query: { range_days: 30 } },
          })
        ).data ?? null
      : null;
    return {
      portal: { companies: items, selected, metrics },
      widgetKeys: [] as string[],
      availableWidgetKeys: [] as string[],
      prefsSource: "none",
      widgetData: {} as Record<string, unknown>,
    };
  }

  // A widget whose loader calls an endpoint the user cannot reach is not "empty", it is a 403.
  const available = dashboardWidgetsFor(enabled, event.locals.user);

  // Prefs only order/filter the (already-known) available widgets, so fetch them alongside the
  // widget data instead of gating on them first — one fewer sequential round-trip (see
  // docs/PERFORMANCE.md). We load all available widgets; the layout then picks/orders.
  const [prefsRes, ...results] = await Promise.all([
    api.GET("/api/v1/dashboard/prefs"),
    ...available.map(async (w) => ({ key: w.key, data: await w.load(api) })),
  ]);
  const prefs = prefsRes.data;
  const layout = prefs?.widgets ?? null;
  const widgets = layout
    ? layout
        .map((key) => available.find((w) => w.key === key))
        .filter((w): w is (typeof available)[number] => Boolean(w))
    : available;

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
