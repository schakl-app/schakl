import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, streamed } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { gtmConnectActions } from "$lib/integrations/google_tag_manager/actions.server";
import { marketingConnectActions } from "$lib/modules/marketing/actions.server";
import { filtersFromUrl } from "$lib/modules/marketing/leads/url";
import type { AiSearchOverview } from "$lib/modules/marketing/aisearch/types";
import type { LeadsDashboard } from "$lib/modules/marketing/leads/types";

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
  // The reader's dimension filters (`?f=service:autotransport`), handed to the leads read and
  // echoed back so every link on the page keeps them.
  const filters = filtersFromUrl(event.url);

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
  const leadsP = companyId
    ? streamed<LeadsDashboard>(
        api.GET("/api/v1/marketing/companies/{company_id}/leads", {
          params: {
            path: { company_id: companyId },
            query: {
              period: range,
              f: Object.entries(filters).flatMap(([d, vs]) => vs.map((v) => `${d}:${v}`)),
            },
          },
        }),
      )
    : null;
  // SE Ranking's AI Search overview for the picked client (docs/SERANKING.md) — streamed for
  // the same reason the leads are: its first view of a month is a read from SE Ranking.
  const aiSearchP = companyId
    ? streamed<AiSearchOverview>(
        api.GET("/api/v1/marketing/companies/{company_id}/ai-search", {
          params: { path: { company_id: companyId } },
        }),
      )
    : null;
  const clients = await clientsP;
  // A client's own marketing page is their dashboard, not a picker: with one company (the
  // usual case) the picker has one tile that only ever leads here, so the page opens on it.
  // Several companies keep the picker — it is the switcher then.
  const clientRows = clients.data?.rows ?? [];
  const only = clientRows.length === 1 ? clientRows[0] : undefined;
  if (event.locals.user?.isPortal && !companyId && only) {
    // Carry the period and the filters along: a link to "the last month, autotransport only"
    // must still mean that after the redirect picks the client for them.
    const params = new URLSearchParams(event.url.searchParams);
    params.set("company", only.company_id);
    throw redirect(303, `/marketing?${params.toString()}`);
  }

  return {
    clients: clients.data?.rows ?? [],
    clientsTotal: clients.data?.total ?? 0,
    // The tenant's own source names (#446), for the tiles that print a name without a row.
    sourceLabels: clients.data?.source_labels ?? {},
    companyId,
    // Streamed behind the shell (docs/PERFORMANCE.md): the client picker and the period tabs are
    // what the user interacts with, and neither needs a fold of daily metric rows to render.
    metrics: metricsP ? metricsP.then((r) => r.data ?? null) : Promise.resolve(null),
    // The leads dashboard streams beside it (docs/MARKETING.md): a cold read is two GA4
    // batches and three Ads queries, and the shell must not wait for Google.
    leads: leadsP ?? Promise.resolve(null),
    aiSearch: aiSearchP ?? Promise.resolve(null),
    filters,
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
  // The connections half of the same control (#411): Tag Manager is attached through its own
  // module's route, so it needs its own action — and this host has no client in the route, so
  // it is the variant that reads the client off the form.
  ...gtmConnectActions,
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
