import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const q = event.url.searchParams.get("q") || undefined;
  const { data } = await apiFor(event).GET("/api/v1/companies", {
    params: { query: { limit: 200, offset: 0, q } },
  });
  return {
    companies: data?.items ?? [],
    total: data?.total ?? 0,
    statusFilter: event.url.searchParams.get("status") ?? "",
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const website = String(form.get("website") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/companies", {
      body: {
        name,
        website: website || null,
        status: String(form.get("status") ?? "active") as "active",
        custom: {},
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/companies/{company_id}", {
        params: { path: { company_id: id } },
      });
    }
    return { deleted: true };
  },
};
