import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Google Ads: the agency's developer token, the default manager account, the
// account links and the instance-wide write switch. Admin-only. The token is write-only — the
// API reports `developer_token_configured` and never plays the value back.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_ads.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [settings, accounts, companies] = await Promise.all([
    api.GET("/api/v1/google-ads/settings"),
    api.GET("/api/v1/google-ads/accounts"),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
  ]);
  return {
    settings: settings.data ?? null,
    accounts: accounts.data ?? [],
    companies: companies.data?.items ?? [],
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const raw = String(form.get("developer_token") ?? "").trim();
    const { error } = await apiFor(event).PUT("/api/v1/google-ads/settings", {
      body: {
        // Blank keeps the stored secret — the field never displays it, so a save that is about
        // something else must not wipe a working credential. Clearing is its own control.
        developer_token: raw,
        default_login_customer_id:
          String(form.get("default_login_customer_id") ?? "").trim() || null,
        // A checkbox posts its *value*, and an unticked one posts nothing. `checked()` asks
        // about presence, which is the only way of reading it that survives someone changing
        // the control (CLAUDE.md §10).
        writes_enabled: checked(form, "writes_enabled"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  clearToken: async (event) => {
    // The deliberate wipe: an explicit null, distinct from the blank field above.
    const { error } = await apiFor(event).PUT("/api/v1/google-ads/settings", {
      body: { developer_token: null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { cleared: true };
  },

  link: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/google-ads/accounts", {
      body: {
        customer_id: String(form.get("customer_id") ?? "").trim(),
        company_id: String(form.get("company_id") ?? "").trim() || null,
        login_customer_id: String(form.get("login_customer_id") ?? "").trim() || null,
        descriptive_name: String(form.get("descriptive_name") ?? "").trim(),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { linked: true };
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST(
      "/api/v1/google-ads/accounts/{account_id}/verify",
      { params: { path: { account_id: String(form.get("account_id") ?? "") } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // The outcome *is* the row: verify records what Google said either way and never raises,
    // so the reloaded account carries the answer.
    return { verified: true };
  },

  unlink: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE(
      "/api/v1/google-ads/accounts/{account_id}",
      { params: { path: { account_id: String(form.get("account_id") ?? "") } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { unlinked: true };
  },
};
