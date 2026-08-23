import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { gtmActions } from "$lib/integrations/google_tag_manager/actions.server";
import { marketingActions } from "$lib/modules/marketing/actions.server";

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

  // Both fire before either is awaited (docs/PERFORMANCE.md): the metrics call is keyed by the
  // id in the URL, not by anything the company row says, so awaiting the entity in front of it
  // would buy a round-trip and nothing else.
  const companyP = api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } });
  const metricsP = api.GET("/api/v1/marketing/companies/{company_id}/metrics", {
    params: { path: { company_id }, query: { period: range } },
  });
  const company = await companyP;
  if (!company.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    company: company.data,
    // Streamed, not awaited: the period tabs, the picker and the page heading are the shell the
    // user came to interact with, and they need none of this. The metrics read folds two bounded
    // windows of daily rows across every linked source (#312) — the one slow thing on the page.
    metrics: metricsP.then((r) => r.data ?? null),
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
