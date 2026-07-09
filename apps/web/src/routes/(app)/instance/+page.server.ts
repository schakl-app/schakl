import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instance administration (issue #26): instance owners only, and only when the surface is
// enabled on this deployment — both enforced API-side; this guard mirrors them for UX.
export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.isInstanceAdmin) throw redirect(303, "/");
  const api = apiFor(event);
  const [orgs, audit] = await Promise.all([
    api.GET("/api/v1/instance/orgs"),
    api.GET("/api/v1/instance/audit", { params: { query: { limit: 25 } } }),
  ]);
  return { orgs: orgs.data ?? [], audit: audit.data ?? [] };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const slug = String(form.get("slug") ?? "")
      .trim()
      .toLowerCase();
    const ownerEmail = String(form.get("owner_email") ?? "").trim();
    if (!name || !slug) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/instance/orgs", {
      body: { name, slug, owner_email: ownerEmail || null },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.slug ?? parsed.key });
    }
    return { created: true };
  },

  import: async (event) => {
    const form = await event.request.formData();
    const slug = String(form.get("slug") ?? "")
      .trim()
      .toLowerCase();
    const raw = String(form.get("data") ?? "");
    if (!slug || !raw) return fail(400, { error: "errors.required", importError: true });
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw);
    } catch {
      return fail(400, { error: "errors.import_invalid", importError: true });
    }
    const { error } = await apiFor(event).POST("/api/v1/instance/orgs/import", {
      body: { slug, data },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key, importError: true });
    return { imported: true };
  },
};
