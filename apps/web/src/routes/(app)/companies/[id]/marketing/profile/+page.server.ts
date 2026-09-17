import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// The client's measurement profile (docs/MARKETING.md): what the leads dashboard reads GA4 and
// Google Ads through. Manager-only — it is configuration, like linking — and the catalog it
// picks from is the property's own event names and dimensions, read live.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "marketing.link.manage")) {
    throw redirect(303, `/companies/${event.params.id}/marketing`);
  }
  const api = apiFor(event);
  const company_id = event.params.id;
  const companyP = api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } });
  const settingsP = api.GET("/api/v1/marketing/companies/{company_id}/settings", {
    params: { path: { company_id } },
  });
  // The catalog is live Google reads (cached an hour on the API); it streams so the form the
  // manager types into does not wait for a property that answers slowly.
  const catalogP = api
    .GET("/api/v1/marketing/companies/{company_id}/leads/catalog", {
      params: { path: { company_id } },
    })
    .then((r) => r.data ?? null)
    .catch(() => null);
  const [company, settings] = await Promise.all([companyP, settingsP]);
  if (!company.data) throw error(404, { code: "not_found", message: "errors.not_found" });
  return {
    company: company.data,
    settings: settings.data ?? null,
    catalog: catalogP,
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  // The editor posts the whole profile as one JSON value: it is one record, validated whole
  // by the API, and a form of two hundred named inputs would be a second schema to keep in step.
  save: async (event) => {
    const form = await event.request.formData();
    let profile: Record<string, unknown>;
    try {
      profile = JSON.parse(String(form.get("lead_profile") ?? "{}")) as Record<string, unknown>;
    } catch {
      return fail(400, { error: "errors.validation", detail: null });
    }
    const { error: apiError } = await apiFor(event).PUT(
      "/api/v1/marketing/companies/{company_id}/settings",
      { params: { path: { company_id: event.params.id } }, body: { lead_profile: profile } },
    );
    if (apiError) {
      // The API names the field it refused (`fields`) and, for a profile, says why in
      // `details.reason` (§9: details carries literals, never translated text).
      const reason = (apiError as { error?: { details?: { reason?: string } } }).error?.details
        ?.reason;
      return fail(400, { error: apiErrorKey(apiError).key, detail: reason ?? null });
    }
    return { saved: true };
  },
  // An explicit null removes the profile and, with it, the dashboard (CLAUDE.md §18).
  remove: async (event) => {
    const { error: apiError } = await apiFor(event).PUT(
      "/api/v1/marketing/companies/{company_id}/settings",
      { params: { path: { company_id: event.params.id } }, body: { lead_profile: null } },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key, detail: null });
    throw redirect(303, `/companies/${event.params.id}/marketing`);
  },
};
