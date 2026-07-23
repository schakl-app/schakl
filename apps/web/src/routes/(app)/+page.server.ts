import "$lib/modules"; // ensure widgets are registered before we read the registry

import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { dashboardWidgetsFor, type DashboardWidgetSpec } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// A saved layout picks/orders the (already permission-filtered) available widgets; unknown
// keys — another audience's widgets in the org template, a module since disabled — drop out.
function resolveLayout(
  prefs: { widgets?: string[] | null; source?: string } | undefined,
  available: DashboardWidgetSpec[],
): { widgetKeys: string[]; prefsSource: string } {
  const layout = prefs?.widgets ?? null;
  const widgets = layout
    ? layout
        .map((key) => available.find((w) => w.key === key))
        .filter((w): w is DashboardWidgetSpec => Boolean(w))
    : available;
  return { widgetKeys: widgets.map((w) => w.key), prefsSource: prefs?.source ?? "none" };
}

// My Day composes widgets contributed by the enabled modules — the core page stays generic.
// The user's saved layout (or the org template) decides which widgets show, in which order.
export const load: PageServerLoad = async (event) => {
  const enabled = event.locals.theme?.enabledModules ?? [];
  const api = apiFor(event);

  // A widget whose loader calls an endpoint the user cannot reach is not "empty", it is a 403.
  // For a portal login this resolves the *portal* gallery (audience filter, #254).
  const available = dashboardWidgetsFor(enabled, event.locals.user);

  // The client portal's homepage (#193, #254): the same per-viewing-user widget board as staff
  // My Day, offering the portal gallery — its marketing widget carries the companies' curated
  // dashboards (#192 layouts, enforced server-side). The board is the client's to arrange; the
  // marketing widget's *content* is not. The companies list is horizon-scoped by the API.
  if (event.locals.user?.isPortal) {
    const { data: companies } = await api.GET("/api/v1/companies", {
      params: { query: { limit: 50, count: false, sort: "name" } },
    });
    const items = (companies?.items ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      // The client's own logo (#196), served tenant+horizon-scoped by the API.
      logoUrl: c.logo_file_id ? `/api/v1/companies/${c.id}/logo` : null,
    }));
    const selected = event.url.searchParams.get("company") ?? items[0]?.id ?? null;
    // Per-website view (owner feedback): a client with several sites reads them one at a
    // time; filtering is client-side, the payload already carries every link.
    const website = event.url.searchParams.get("website") || "";
    // The marketing widget's data is URL-driven (company/website), so the page injects it
    // below; its registry `load` is a no-op and is skipped here.
    const [prefsRes, metricsRes, ...results] = await Promise.all([
      api.GET("/api/v1/dashboard/prefs"),
      selected
        ? api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
            params: { path: { company_id: selected }, query: { range_days: 30 } },
          })
        : Promise.resolve(null),
      ...available
        .filter((w) => w.key !== "marketing.portal")
        .map(async (w) => ({ key: w.key, data: await w.load(api) })),
    ]);
    const widgetData: Record<string, unknown> = {
      "marketing.portal": { companyId: selected, website, metrics: metricsRes?.data ?? null },
    };
    for (const r of results) widgetData[r.key] = r.data;
    return {
      portal: { companies: items, selected },
      ...resolveLayout(prefsRes.data, available),
      availableWidgetKeys: available.map((w) => w.key),
      widgetData,
    };
  }

  // Prefs only order/filter the (already-known) available widgets, so fetch them alongside the
  // widget data instead of gating on them first — one fewer sequential round-trip (see
  // docs/PERFORMANCE.md). We load all available widgets; the layout then picks/orders.
  const [prefsRes, ...results] = await Promise.all([
    api.GET("/api/v1/dashboard/prefs"),
    ...available.map(async (w) => ({ key: w.key, data: await w.load(api) })),
  ]);
  const widgetData: Record<string, unknown> = {};
  for (const r of results) widgetData[r.key] = r.data;

  return {
    portal: null,
    ...resolveLayout(prefsRes.data, available),
    availableWidgetKeys: available.map((w) => w.key),
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
