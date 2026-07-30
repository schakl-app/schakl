import "$lib/modules"; // ensure widgets are registered before we read the registry

import { fail } from "@sveltejs/kit";

import { dedupeGets } from "$lib/core/api/dedupe";
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
  // Two selected widgets may request the exact same digest (the invoicing tiles do), and the
  // registry deliberately keeps them ignorant of each other — see `dedupeGets`.
  const api = dedupeGets(apiFor(event));

  // A widget whose loader calls an endpoint the user cannot reach is not "empty", it is a 403.
  // For a portal login this resolves the *portal* gallery (audience filter, #254).
  const available = dashboardWidgetsFor(enabled, event.locals.user);

  // The client portal's homepage (#193, #254): the same per-viewing-user widget board as staff
  // My Day, offering the portal gallery — its marketing widget carries the companies' curated
  // dashboards (#192 layouts, enforced server-side). The board is the client's to arrange; the
  // marketing widget's *content* is not. The companies list is horizon-scoped by the API.
  if (event.locals.user?.isPortal) {
    const [{ data: companies }, prefsRes] = await Promise.all([
      api.GET("/api/v1/companies", {
        params: { query: { limit: 50, count: false, sort: "name" } },
      }),
      api.GET("/api/v1/dashboard/prefs"),
    ]);
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
    const layout = resolveLayout(prefsRes.data, available);
    const selectedWidgets = available.filter((w) => layout.widgetKeys.includes(w.key));
    // The marketing widget's data is URL-driven (company/website), so the page injects it
    // below; its registry `load` is a no-op and is skipped here.
    // Promises are intentionally returned, not awaited: SvelteKit streams each tile as it
    // resolves, so a slow marketing/finance digest cannot hold back the board shell or peers.
    const widgetData: Record<string, Promise<unknown>> = {};
    if (layout.widgetKeys.includes("marketing.portal")) {
      widgetData["marketing.portal"] = (
        selected
          ? api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
              params: { path: { company_id: selected }, query: { range_days: 30 } },
            })
          : Promise.resolve(null)
      )
        .then((metricsRes) => ({
          companyId: selected,
          website,
          metrics: metricsRes?.data ?? null,
        }))
        .catch(() => ({ companyId: selected, website, metrics: null }));
    }
    for (const widget of selectedWidgets.filter((item) => item.key !== "marketing.portal")) {
      widgetData[widget.key] = widget.load(api).catch(() => null);
    }
    return {
      portal: { companies: items, selected },
      ...layout,
      availableWidgetKeys: available.map((w) => w.key),
      widgetData,
    };
  }

  // Resolve the saved board before loading data. This costs one small prefs round-trip up front,
  // but avoids running every hidden widget — several widgets fan out into multiple aggregate
  // requests, so a deliberately small board used to be as slow as the largest possible one.
  const prefsRes = await api.GET("/api/v1/dashboard/prefs");
  const layout = resolveLayout(prefsRes.data, available);
  const selectedWidgets = available.filter((w) => layout.widgetKeys.includes(w.key));
  // Stream tiles independently. Full dashboards become progressively useful instead of waiting
  // for the slowest selected module before sending any page content.
  const widgetData: Record<string, Promise<unknown>> = {};
  for (const widget of selectedWidgets) {
    widgetData[widget.key] = widget.load(api).catch(() => null);
  }

  return {
    portal: null,
    ...layout,
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
