import { fail } from "@sveltejs/kit";

import { apiErrorKey, type ApiError } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Per-org SSO / OIDC (#76): DB-stored, UI-configured — no env vars, no restart. Admin-only
// (the API enforces `settings.auth.manage`). The client secret is write-only: the API reports
// `secret_configured` and never plays the value back.
export const load: PageServerLoad = async (event) => {
  const { data } = await apiFor(event).GET("/api/v1/settings/sso");
  return { sso: data ?? null, locale: event.locals.locale };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const text = (name: string) => String(form.get(name) ?? "").trim() || null;
    const name = text("name");
    const default_role = text("default_role");
    if (!name || !default_role)
      return fail(400, { error: "errors.required", fields: undefined as ApiError["fields"] });

    const { error } = await apiFor(event).PUT("/api/v1/settings/sso", {
      body: {
        enabled: form.get("enabled") === "on",
        enforced: form.get("enforced") === "on",
        name,
        discovery_url: text("discovery_url"),
        client_id: text("client_id"),
        // Empty means "keep the stored secret" — the API never returns it.
        client_secret: text("client_secret"),
        default_role,
        auto_provision: form.get("auto_provision") === "on",
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },

  test: async (event) => {
    const { data, error } = await apiFor(event).POST("/api/v1/settings/sso/test");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { test: data };
  },
};
