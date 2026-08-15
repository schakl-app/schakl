import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { marketingConnectActions } from "$lib/modules/marketing/actions.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // The API enforces the permission too; redirect a member who lacks it rather than showing a
  // bare page (the nav item is already hidden for them).
  if (!can(event.locals.user, "marketing.metrics.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const companyId = event.url.searchParams.get("company") || "";
  const range = event.url.searchParams.get("range") ?? "30d";
  // Website filter: "" = everything, "client" = client-level links only, else a website id.
  // Filtering happens client-side — the metrics payload already carries every link.
  const website = event.url.searchParams.get("website") || "";

  // The picker's tiles: the clients that actually have a source linked, with which sources and
  // how each is doing. Deliberately not `/companies` — the old dropdown listed every company,
  // and most of its entries led to an empty dashboard with nothing on the way there saying so.
  // It is also cheaper: one query over the links, no metric fold (docs/PERFORMANCE.md).
  const clientsP = api.GET("/api/v1/marketing/clients", { params: { query: { limit: 200 } } });
  const metricsP = companyId
    ? api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
        params: { path: { company_id: companyId }, query: { period: range } },
      })
    : null;
  const clients = await clientsP;

  return {
    clients: clients.data?.rows ?? [],
    clientsTotal: clients.data?.total ?? 0,
    companyId,
    // Streamed behind the shell (docs/PERFORMANCE.md): the client picker and the period tabs are
    // what the user interacts with, and neither needs a fold of daily metric rows to render.
    metrics: metricsP ? metricsP.then((r) => r.data ?? null) : Promise.resolve(null),
    range,
    website,
    // Whether to draw the ＋ (#338). The client list behind its picker is fetched by the dialog
    // on first open, so this page still loads exactly what it loaded before.
    canLink: can(event.locals.user, "marketing.link.manage"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  // Connecting a source from here rather than sending the user to `/companies` to find the
  // client and then the gesture (#338). Same write as the client page's panel.
  ...marketingConnectActions,
  createCompany: createCompanyAction,
  // Save the client's curated layout (#192) — the same action the client tab's dashboard posts,
  // so editing works identically on both surfaces. The API enforces marketing.link.manage.
  saveLayout: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "");
    if (!company_id) return fail(400, { error: "errors.validation" });
    let layout: Record<string, unknown>;
    try {
      layout = JSON.parse(String(form.get("layout") ?? "{}")) as Record<string, unknown>;
    } catch {
      return fail(400, { error: "errors.validation" });
    }
    const { error: apiError } = await apiFor(event).PUT(
      "/api/v1/marketing/companies/{company_id}/settings",
      { params: { path: { company_id } }, body: { layout } },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { saved: true };
  },
  // The same comparison write as the client tab's dashboard, so editing works identically on
  // both surfaces (#312). An empty value posts an explicit `null` — that is what clears the
  // override back to the org default; omitting it would mean "leave alone".
  saveCompare: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "");
    if (!company_id) return fail(400, { error: "errors.validation" });
    const raw = String(form.get("compare") ?? "");
    if (raw && raw !== "year" && raw !== "previous")
      return fail(400, { error: "errors.validation" });
    const { error: apiError } = await apiFor(event).PUT(
      "/api/v1/marketing/companies/{company_id}/settings",
      {
        params: { path: { company_id } },
        body: { compare: raw ? (raw as "year" | "previous") : null },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { saved: true };
  },
};
