import { fail, redirect, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { COMMON_CURRENCIES, otherCurrencies } from "$lib/core/currencies";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { COMMON_EUROPEAN_TIMEZONES, otherTimeZones } from "$lib/core/timezones";

import type { Actions, PageServerLoad } from "./$types";

/** Upload a branding asset through the storage core (#123). Multipart goes through a plain
 *  fetch — the typed client has no multipart serializer — with the same cookie + tenant host
 *  the client would send. Returns the *public* serve URL: branding renders on the login
 *  screen before a session exists. */
async function uploadBrandingFile(
  event: RequestEvent,
  upload: File,
): Promise<{ url: string } | { error: string }> {
  const body = new FormData();
  body.append("file", upload, upload.name);
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/files?entity_type=branding`, {
    method: "POST",
    headers: {
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
    body,
  });
  if (!res.ok) {
    return { error: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type" };
  }
  const meta = (await res.json()) as { id: string };
  return { url: `/api/v1/files/${meta.id}/public` };
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.branding.write")) throw redirect(303, "/");
  const api = apiFor(event);
  const [{ data }, { data: domain }] = await Promise.all([
    api.GET("/api/v1/meta/tenant"),
    api.GET("/api/v1/meta/tenant/domain"),
  ]);
  return {
    branding: data ?? null,
    domain: domain ?? null,
    locales: ["nl", "en"],
    commonTimezones: [...COMMON_EUROPEAN_TIMEZONES],
    otherTimezones: otherTimeZones(),
    commonCurrencies: [...COMMON_CURRENCIES],
    otherCurrencies: otherCurrencies(),
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const brand_name = String(form.get("brand_name") ?? "").trim();
    if (!brand_name) return fail(400, { error: "errors.required" });

    // A chosen file wins over the URL field; either way the stored value is just a URL.
    let logo_url = String(form.get("logo_url") ?? "").trim();
    let favicon_url = String(form.get("favicon_url") ?? "").trim();
    const logoFile = form.get("logo_file");
    if (logoFile instanceof File && logoFile.size > 0) {
      const uploaded = await uploadBrandingFile(event, logoFile);
      if ("error" in uploaded) return fail(400, { error: uploaded.error });
      logo_url = uploaded.url;
    }
    const faviconFile = form.get("favicon_file");
    if (faviconFile instanceof File && faviconFile.size > 0) {
      const uploaded = await uploadBrandingFile(event, faviconFile);
      if ("error" in uploaded) return fail(400, { error: uploaded.error });
      favicon_url = uploaded.url;
    }
    // The installable-app icon (#198): a square raster the manifest/apple-touch derive from.
    let app_icon_url = String(form.get("app_icon_url") ?? "").trim();
    const appIconFile = form.get("app_icon_file");
    if (appIconFile instanceof File && appIconFile.size > 0) {
      const uploaded = await uploadBrandingFile(event, appIconFile);
      if ("error" in uploaded) return fail(400, { error: uploaded.error });
      app_icon_url = uploaded.url;
    }

    const { error } = await apiFor(event).PATCH("/api/v1/meta/tenant", {
      body: {
        brand_name,
        show_brand_name: form.get("show_brand_name") === "on",
        logo_url,
        favicon_url,
        app_icon_url,
        primary_color: String(form.get("primary_color") ?? "").trim() || undefined,
        accent_color: String(form.get("accent_color") ?? "").trim() || undefined,
        default_locale: String(form.get("default_locale") ?? "").trim() || undefined,
        timezone: String(form.get("timezone") ?? "").trim() || undefined,
        currency: String(form.get("currency") ?? "").trim() || undefined,
        // Empty clears the template back to the built-in format (#97).
        tab_title_template: String(form.get("tab_title_template") ?? "").trim(),
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
