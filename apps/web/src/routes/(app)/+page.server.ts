import "$lib/modules"; // ensure widgets are registered before we read the registry

import { dashboardWidgetsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

// My Day composes widgets contributed by the enabled modules — the core page stays generic.
export const load: PageServerLoad = async (event) => {
  const enabled = event.locals.theme?.enabledModules ?? [];
  const widgets = dashboardWidgetsFor(enabled);
  const api = apiFor(event);

  const results = await Promise.all(
    widgets.map(async (w) => ({ key: w.key, data: await w.load(api) })),
  );
  const widgetData: Record<string, unknown> = {};
  for (const r of results) widgetData[r.key] = r.data;

  return { widgetKeys: widgets.map((w) => w.key), widgetData };
};
