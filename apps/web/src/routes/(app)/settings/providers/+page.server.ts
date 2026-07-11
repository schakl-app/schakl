import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.providers.manage")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/providers", {
    params: { query: { include_inactive: true } },
  });
  return { providers: data ?? [] };
};

function providerBody(form: FormData) {
  return {
    kind: String(form.get("kind") ?? "email") as "email" | "dns" | "registrar" | "hosting",
    name: String(form.get("name") ?? "").trim(),
    position: Number(form.get("position") ?? 0) || 0,
  };
}

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const provider_id = String(form.get("id") ?? "");
    const body = providerBody(form);
    if (!body.name) return fail(400, { error: "errors.required" });

    if (provider_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/providers/{provider_id}", {
        params: { path: { provider_id } },
        body: { name: body.name, position: body.position },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const { error } = await apiFor(event).POST("/api/v1/providers", {
        body: { ...body, active: true },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const provider_id = String(form.get("id") ?? "");
    if (provider_id) {
      await apiFor(event).PATCH("/api/v1/providers/{provider_id}", {
        params: { path: { provider_id } },
        body: { active: form.get("active") === "true" },
      });
    }
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const provider_id = String(form.get("id") ?? "");
    if (provider_id) {
      await apiFor(event).DELETE("/api/v1/providers/{provider_id}", {
        params: { path: { provider_id } },
      });
    }
    return { deleted: true };
  },
};
