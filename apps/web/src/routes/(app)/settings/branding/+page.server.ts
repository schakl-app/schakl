import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/meta/tenant");
  return { branding: data ?? null, locales: ["nl", "en"] };
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
};
