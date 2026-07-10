import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // Manager-only screen (label writes themselves only need staff access).
  if (!can(event.locals.user, "tasks.label.write")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/tasks/labels");
  return { labels: data ?? [] };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/tasks/labels", {
      body: {
        name,
        color: String(form.get("color") ?? "blue"),
        position: Number(form.get("position") ?? 0) || 0,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const label_id = String(form.get("id") ?? "");
    if (!label_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/labels/{label_id}", {
      params: { path: { label_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        color: String(form.get("color") ?? "") || undefined,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const label_id = String(form.get("id") ?? "");
    if (label_id) {
      await apiFor(event).DELETE("/api/v1/tasks/labels/{label_id}", {
        params: { path: { label_id } },
      });
    }
    return { deleted: true };
  },
};
