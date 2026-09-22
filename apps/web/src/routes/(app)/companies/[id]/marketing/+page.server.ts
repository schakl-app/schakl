import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, streamed } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { headStart } from "$lib/core/streaming";
import { gtmActions } from "$lib/integrations/google_tag_manager/actions.server";
import { marketingActions } from "$lib/modules/marketing/actions.server";
import { filtersFromUrl } from "$lib/modules/marketing/leads/url";
import type { AiSearchOverview } from "$lib/modules/marketing/aisearch/types";
import type { LeadsDashboard } from "$lib/modules/marketing/leads/types";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // The API enforces the permission; redirect a member who lacks it back to the client page
  // rather than showing an empty tab.
  if (!can(event.locals.user, "marketing.metrics.read")) {
    throw redirect(303, `/companies/${event.params.id}`);
  }
  const api = apiFor(event);
  const company_id = event.params.id;
  const range = event.url.searchParams.get("range") ?? "30d";
  // Website filter: "" = everything, "client" = client-level links only, else a website id.
  const website = event.url.searchParams.get("website") || "";
  const filters = filtersFromUrl(event.url);

  // Both fire before either is awaited (docs/PERFORMANCE.md): the metrics call is keyed by the
  // id in the URL, not by anything the company row says, so awaiting the entity in front of it
  // would buy a round-trip and nothing else.
  const companyP = api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } });
  const metricsP = api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
    params: { path: { company_id }, query: { period: range } },
  });
  const leadsP = streamed<LeadsDashboard>(
    api.GET("/api/v1/marketing/companies/{company_id}/leads", {
      params: {
        path: { company_id },
        query: {
          period: range,
          f: Object.entries(filters).flatMap(([d, vs]) => vs.map((v) => `${d}:${v}`)),
        },
      },
    }),
  );
  // SE Ranking's AI Search overview (docs/SERANKING.md). Streamed, and started before anything
  // is awaited: the first view of a month is a paid read from SE Ranking, seconds of somebody
  // else's latency; every view after it is one indexed query.
  const aiSearchP = streamed<AiSearchOverview>(
    api.GET("/api/v1/marketing/companies/{company_id}/ai-search", {
      params: { path: { company_id } },
    }),
  );
  const company = await companyP;
  if (!company.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  // Streamed, not awaited — but with a head start (`$lib/core/streaming`): the tiles are a
  // stored read, the leads dashboard and the AI Search overview are a Redis hit on every
  // ordinary open (the nightly warm, docs/MARKETING.md), so all three usually answer well inside
  // the budget and ship *in* the shell, drawn once in their final shape. A cold one — Google's
  // latency on a view nobody has opened today, SE Ranking's on a month not stored yet — is
  // returned as its promise and streams behind the shell exactly as before, into a placeholder
  // the size of what it becomes. The budget is the most a cold read may delay the shell.
  const started = await headStart({
    // The metrics read folds two bounded windows of daily rows across every linked source
    // (#312); the period tabs, the picker and the page heading need none of it.
    metrics: metricsP.then((r) => r.data ?? null),
    // The leads dashboard (docs/MARKETING.md).
    leads: leadsP,
    aiSearch: aiSearchP,
  });

  return {
    company: company.data,
    leads: started.leads,
    aiSearch: started.aiSearch,
    filters,
    metrics: started.metrics,
    range,
    website,
    // Whether to draw the ＋ (#399). This tab used to offer nothing at all on a client with no
    // links: its one empty state pointed at the client page, where the gesture lives behind
    // ⋯ → Bewerken, and with no Google grant anywhere in the org it did not even do that.
    // The client list behind the dialog is not fetched here — the route *is* the client.
    canLink: can(event.locals.user, "marketing.link.manage"),
  };
};

export const actions: Actions = {
  // The same writes the client page's panel posts (#338/#399) — `marketingActions` reads the
  // client off `event.params.id`, which this route has too, so the dialog needs no second
  // answer for which client it is attaching to. `gtmActions` brings the connections half.
  ...marketingActions,
  ...gtmActions,
  // Save the client's curated tab layout (#192). The editor posts the whole layout —
  // its own source replaced, the others carried through — as one JSON value.
  saveLayout: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? event.params.id);
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
  // What this client's dashboard measures against (#312). An empty value is *sent* as `null`,
  // never omitted: omitting means "leave alone" at the API, so a user picking "volg de standaard"
  // would silently keep whatever override was there.
  saveCompare: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? event.params.id);
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
