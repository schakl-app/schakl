import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // The API enforces it too; redirect rather than showing a bare page — the nav item is already
  // hidden for anyone without the permission (docs/UX.md).
  if (!can(event.locals.user, "google_tag_manager.container.read")) throw redirect(303, "/");
  const api = apiFor(event);

  // Containers and clients in one fan: the list is small (one row per linked container) and the
  // client names are what turn a GTM id into something a human recognises. `count: false`
  // because nothing on this page shows a total (docs/PERFORMANCE.md).
  const [containers, companies] = await Promise.all([
    api.GET("/api/v1/gtm/containers", { params: { query: { active_only: true } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
  ]);

  return {
    containers: containers.data ?? [],
    companies: companies.data?.items ?? [],
    canManage: can(event.locals.user, "google_tag_manager.settings.manage"),
  };
};

export const actions: Actions = {
  /**
   * Linking from here rather than sending everyone to Instellingen. The control mirrors the key
   * the call actually makes (#310): `POST /gtm/containers` declares `settings.manage`, so that
   * is what `canManage` gates — not the read permission this page is *about*.
   */
  link: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/gtm/containers", {
      body: {
        public_id: String(form.get("public_id") ?? "").trim(),
        company_id: String(form.get("company_id") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { linked: true };
  },
};
