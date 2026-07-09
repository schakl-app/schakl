import { fail, redirect } from "@sveltejs/kit";

import { apiLogin, AUTH_COOKIE_NAME } from "$lib/core/auth.server";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// First-run wizard (issue #26): only reachable while the instance has no org at all —
// the API refuses a second setup regardless, this guard is just the UX mirror of that.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const { data: status } = await api.GET("/api/v1/setup/status");
  if (!status?.needs_setup) throw redirect(303, "/");
  const { data: modules } = await api.GET("/api/v1/meta/modules");
  return {
    availableModules: modules?.enabled_modules ?? ["companies"],
    locales: modules?.supported_locales ?? ["nl", "en"],
    defaultLocale: modules?.default_locale ?? "nl",
  };
};

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const values = {
      org_name: String(form.get("org_name") ?? "").trim(),
      slug: String(form.get("slug") ?? "")
        .trim()
        .toLowerCase(),
      brand_name: String(form.get("brand_name") ?? "").trim(),
      locale: String(form.get("locale") ?? "").trim(),
      owner_full_name: String(form.get("owner_full_name") ?? "").trim(),
      owner_email: String(form.get("owner_email") ?? "").trim(),
    };
    const password = String(form.get("owner_password") ?? "");
    const modules = form.getAll("modules").map(String);

    if (!values.org_name || !values.slug || !values.owner_email || !password) {
      return fail(400, {
        error: "errors.required",
        fields: undefined as Record<string, string> | undefined,
        values,
      });
    }

    const { error } = await apiFor(event).POST("/api/v1/setup", {
      body: {
        org_name: values.org_name,
        slug: values.slug,
        brand_name: values.brand_name || undefined,
        primary_color: String(form.get("primary_color") ?? "").trim() || undefined,
        accent_color: String(form.get("accent_color") ?? "").trim() || undefined,
        locale: values.locale || undefined,
        enabled_modules: modules.length ? modules : undefined,
        owner_email: values.owner_email,
        owner_password: password,
        owner_full_name: values.owner_full_name || undefined,
      },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.key, fields: parsed.fields, values });
    }

    // Sign the fresh owner in on this host (the wizard just claimed it for the org).
    const token = await apiLogin(event, values.owner_email, password);
    if (!token) {
      return fail(400, { error: "auth.invalid_credentials", fields: undefined, values });
    }
    event.cookies.set(AUTH_COOKIE_NAME, token, {
      path: "/",
      httpOnly: true,
      sameSite: "lax",
      secure: event.url.protocol === "https:",
      maxAge: 60 * 60 * 24 * 7,
    });
    throw redirect(303, "/");
  },
};
