import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.branding.write")) throw redirect(303, "/");
  const api = apiFor(event);
  const [{ data }, { data: domain }] = await Promise.all([
    api.GET("/api/v1/meta/tenant"),
    api.GET("/api/v1/meta/tenant/domain"),
  ]);
  return { branding: data ?? null, domain: domain ?? null, locales: ["nl", "en"] };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const brand_name = String(form.get("brand_name") ?? "").trim();
    if (!brand_name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/meta/tenant", {
      body: {
        brand_name,
        show_brand_name: form.get("show_brand_name") === "on",
        logo_url: String(form.get("logo_url") ?? "").trim(),
        favicon_url: String(form.get("favicon_url") ?? "").trim(),
        primary_color: String(form.get("primary_color") ?? "").trim() || undefined,
        accent_color: String(form.get("accent_color") ?? "").trim() || undefined,
        default_locale: String(form.get("default_locale") ?? "").trim() || undefined,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  // Custom domain (issue #26): claim → prove control via DNS TXT → it starts resolving.
  claimDomain: async (event) => {
    const form = await event.request.formData();
    const domain = String(form.get("domain") ?? "")
      .trim()
      .toLowerCase();
    if (!domain) return fail(400, { error: "errors.required", domainError: true });
    const { error } = await apiFor(event).POST("/api/v1/meta/tenant/domain", {
      body: { domain },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.domain ?? parsed.key, domainError: true });
    }
    return { domainClaimed: true };
  },

  verifyDomain: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/meta/tenant/domain/verify");
    if (error) return fail(400, { error: apiErrorKey(error).key, domainError: true });
    return { domainVerified: true };
  },

  clearDomain: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/meta/tenant/domain");
    if (error) return fail(400, { error: apiErrorKey(error).key, domainError: true });
    return { domainCleared: true };
  },
};
