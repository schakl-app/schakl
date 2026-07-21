import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // Manager-only screen (status writes need the dedicated permission — issue #62).
  if (!can(event.locals.user, "tasks.status.write")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/tasks/statuses");
  return { statuses: data ?? [] };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    // A tenant types a name; the key is an immutable slug derived from it.
    const key = slugify(name);
    if (!key) return fail(400, { error: "errors.label_no_key" });
    const { error, response } = await apiFor(event).POST("/api/v1/tasks/statuses", {
      body: {
        key,
        name,
        color: String(form.get("color") ?? "blue"),
        position: Number(form.get("position") ?? 0) || 0,
        is_terminal: form.get("is_terminal") === "true",
        is_default: form.get("is_default") === "true",
        requires_interaction: form.get("requires_interaction") === "true",
      },
    });
    if (error) return fail(400, { error: createErrorKey(error, response) });
    return { created: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const status_id = String(form.get("id") ?? "");
    if (!status_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/statuses/{status_id}", {
      params: { path: { status_id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        color: String(form.get("color") ?? "") || undefined,
        is_terminal: form.get("is_terminal") === "true",
        is_default: form.get("is_default") === "true",
        requires_interaction: form.get("requires_interaction") === "true",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const status_id = String(form.get("id") ?? "");
    if (!status_id) return fail(400, { error: "errors.required" });
    // Deletion can be refused (last status, or one still holding tasks) — surface that.
    const { error } = await apiFor(event).DELETE("/api/v1/tasks/statuses/{status_id}", {
      params: { path: { status_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
