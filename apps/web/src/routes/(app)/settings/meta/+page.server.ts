import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Meta: the agency's own Meta app, the system-user tokens, and the two
 * decisions about publishing — who holds the clock for a Facebook post, and whether anything
 * may be published at all.
 *
 * Admin-only, and it holds **credentials and posture, nothing else**: which Page is which
 * client's is decided where the work is (Marketing → Social → Kanalen), and this screen
 * points there.
 *
 * Both secrets are write-only. The API reports `app_secret_configured` and never plays a
 * value back; a token is only ever replaced, never shown.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meta.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const adsEnabled = event.locals.theme?.enabledModules?.includes("meta_ads") ?? false;
  const mayPolicy = adsEnabled && can(event.locals.user, "meta_ads.policy.manage");
  const [settings, credentials, status, adsSettings] = await Promise.all([
    api.GET("/api/v1/meta-business/settings"),
    api.GET("/api/v1/meta-business/credentials"),
    api.GET("/api/v1/meta-business/status"),
    // Only asked for by a caller who may change it (#310: mirror the key the call makes).
    mayPolicy ? api.GET("/api/v1/meta-ads/settings") : null,
  ]);
  return {
    settings: settings.data ?? null,
    credentials: credentials.data ?? [],
    status: status.data ?? null,
    adsSettings: adsSettings?.data ?? null,
    mayPolicy,
    loadError: settings.data ? null : apiErrorKey(settings.error, "errors.server").key,
  };
};

function refusal(error: unknown, scope: string) {
  const { key, fields } = apiErrorKey(error, "errors.server");
  return fail(400, { error: key, fields, scope });
}

export const actions: Actions = {
  /** The app. A blank secret keeps the stored one; clearing it is its own control. */
  saveApp: async (event) => {
    const form = await event.request.formData();
    const secret = String(form.get("app_secret") ?? "").trim();
    const { error } = await apiFor(event).PUT("/api/v1/meta-business/settings", {
      body: {
        app_id: String(form.get("app_id") ?? "").trim() || null,
        ...(secret ? { app_secret: secret } : {}),
      },
    });
    if (error) return refusal(error, "app");
    return { saved: "app" };
  },

  clearSecret: async (event) => {
    const { error } = await apiFor(event).PUT("/api/v1/meta-business/settings", {
      body: { app_secret: null },
    });
    if (error) return refusal(error, "app");
    return { saved: "app" };
  },

  savePublishing: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/meta-business/settings", {
      body: {
        facebook_scheduler: form.get("facebook_scheduler") === "meta" ? "meta" : "schakl",
        // A checkbox posts its value and an unticked one posts nothing: `checked()` asks
        // about presence (CLAUDE.md §10).
        writes_enabled: checked(form, "writes_enabled"),
      },
    });
    if (error) return refusal(error, "publishing");
    if (form.has("ads_present")) {
      const ads = await apiFor(event).PUT("/api/v1/meta-ads/settings", {
        body: { writes_enabled: checked(form, "ads_writes_enabled") },
      });
      if (ads.error) return refusal(ads.error, "publishing");
    }
    return { saved: "publishing" };
  },

  addToken: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/meta-business/credentials", {
      body: {
        label: String(form.get("label") ?? "").trim(),
        token: String(form.get("token") ?? "").trim(),
        business_id: String(form.get("business_id") ?? "").trim(),
      },
    });
    if (error || !data) return refusal(error, "token");
    // A token that answers is asked at once what it reaches: the next thing anybody wants
    // after adding one is the list of channels, and a second press to get it is a press for
    // nothing. A failure here is the discovery's own and is said on the row.
    let found: number | null = null;
    let warnings: string[] = [];
    if (data.status === "active") {
      const discovery = await apiFor(event).POST(
        "/api/v1/meta-business/credentials/{credential_id}/discover",
        { params: { path: { credential_id: data.id } } },
      );
      found = discovery.data?.found ?? null;
      warnings = discovery.data?.warnings ?? [];
    }
    return { saved: "token", added: data.id, found, warnings };
  },

  replaceToken: async (event) => {
    const form = await event.request.formData();
    const token = String(form.get("token") ?? "").trim();
    const label = String(form.get("label") ?? "").trim();
    const { error } = await apiFor(event).PATCH(
      "/api/v1/meta-business/credentials/{credential_id}",
      {
        params: { path: { credential_id: String(form.get("credential_id") ?? "") } },
        body: {
          ...(label ? { label } : {}),
          ...(token ? { token } : {}),
          business_id: String(form.get("business_id") ?? "").trim(),
        },
      },
    );
    if (error) return refusal(error, "token");
    return { saved: "token" };
  },

  verify: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST(
      "/api/v1/meta-business/credentials/{credential_id}/verify",
      { params: { path: { credential_id: String(form.get("credential_id") ?? "") } } },
    );
    if (error) return refusal(error, "token");
    // The outcome *is* the row: verify records what Meta said either way.
    return { saved: "verified" };
  },

  refresh: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/meta-business/credentials/{credential_id}/refresh",
      { params: { path: { credential_id: String(form.get("credential_id") ?? "") } } },
    );
    if (error) return refusal(error, "token");
    return { saved: data?.refresh_error ? "refresh_failed" : "refreshed" };
  },

  discover: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/meta-business/credentials/{credential_id}/discover",
      { params: { path: { credential_id: String(form.get("credential_id") ?? "") } } },
    );
    if (error) return refusal(error, "token");
    return {
      saved: "discovered",
      found: data?.found ?? 0,
      created: data?.created ?? 0,
      warnings: data?.warnings ?? [],
    };
  },

  remove: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE(
      "/api/v1/meta-business/credentials/{credential_id}",
      { params: { path: { credential_id: String(form.get("credential_id") ?? "") } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error, "errors.server").key });
    return { saved: "removed" };
  },
};
